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

import copy
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from graphlib import CycleError
from pathlib import Path
from urllib.request import pathname2url
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


@dataclass(frozen=True)
class StatusChangeHook:
    name: str
    fn: Callable[[sqlite3.Connection, str, str | None, str], None]


_status_change_hooks: list[StatusChangeHook] = []


def register_status_change_hook(
    name: str,
    fn: Callable[[sqlite3.Connection, str, str | None, str], None],
) -> None:
    """Register a hook that runs when an entity's status is CHANGED through a
    domain update function.

    Hooks run inside the caller's transaction, before the final commit, so the
    status change and any hook side-effects land atomically. Name-keyed so module
    re-import is a no-op (same discipline as register_post_add_hook).

    The update-side twin of register_post_add_hook: the create side already
    existed, the update side did not.
    """
    if any(h.name == name for h in _status_change_hooks):
        return
    _status_change_hooks.append(StatusChangeHook(name, fn))


def run_status_change_hooks(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Invoke every registered status-change hook. Failures are logged but never
    raised — a status write must always succeed.

    CONTRACT FOR CALLERS: call this ONLY when the status write actually changed
    the row (a status was requested, rowcount == 1, and the value differs).
    """
    for hook in _status_change_hooks:
        try:
            hook.fn(conn, entity_id, old_status, new_status)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[status-change hook '{hook.name}' failed] {e}\n")


# --- Pre-add resolver seam (CB-45) ---


@dataclass(frozen=True)
class PreAddResolver:
    """A registered pre-add resolver: annotates a new finding before its INSERT.

    ANNOTATE-ONLY by construction: the resolver returns a meta patch (or None);
    there is no redirect channel — identity routing is core (CB-44, ratified).
    """

    name: str
    fn: Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any] | None]
    meta_keys: frozenset[str]
    updatable_keys: frozenset[str]


_pre_add_resolvers: list[PreAddResolver] = []

_RESOLVER_ERRORS_KEY = "resolver_errors"


class _ResolverBrokeTransaction(Exception):
    """Internal sentinel: a resolver closed the caller's transaction."""


def register_pre_add_resolver(
    name: str,
    fn: Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any] | None],
    *,
    meta_keys: tuple[str, ...],
    updatable_keys: tuple[str, ...] = (),
) -> None:
    """Register a pre-add resolver.

    Same discipline as the other HOOK registries (post-add, status-change): an
    identical re-registration is a silent no-op so module re-import is safe —
    but a same-name registration with DIFFERENT meta_keys raises, because a
    silently ignored contract change is CB-15's failure shape, and a same-name
    registration with a different FUNCTION raises for the same reason: the new
    implementation would never run while its caller believes it registered
    (Codex review of this range). `meta_keys` declares the ONLY meta keys this
    resolver's annotation may write; findings reserves the union against
    caller-supplied meta. `updatable_keys` (a subset) declares which of those
    stay writable via update — an annotation that can never be repaired is the
    CB-26 shape, so a resolver whose stamp is advisory should declare it here.
    Overlap with another resolver's keys is refused (CB-16's
    last-assignment-wins, at the seam level).
    """
    keys = frozenset(meta_keys)
    updatable = frozenset(updatable_keys)
    if not updatable <= keys:
        raise ValueError(
            f"updatable_keys {sorted(updatable - keys)} not declared in meta_keys"
        )
    for existing in _pre_add_resolvers:
        if existing.name == name:
            if existing.meta_keys != keys or existing.updatable_keys != updatable:
                raise ValueError(
                    f"resolver {name!r} re-registered with different meta_keys "
                    f"{sorted(keys)} (was {sorted(existing.meta_keys)})"
                )
            if existing.fn is not fn:
                raise ValueError(
                    f"resolver {name!r} already registered with a different function; "
                    f"a silently ignored implementation would never run"
                )
            return
    if _RESOLVER_ERRORS_KEY in keys:
        raise ValueError(f"meta key {_RESOLVER_ERRORS_KEY!r} is reserved for the runner")
    for existing in _pre_add_resolvers:
        overlap = keys & existing.meta_keys
        if overlap:
            raise ValueError(
                f"meta keys {sorted(overlap)} already declared by resolver {existing.name!r}"
            )
    _pre_add_resolvers.append(PreAddResolver(name, fn, keys, updatable))


def resolver_reserved_meta_keys() -> frozenset[str]:
    """Every meta key any registered resolver may write, plus the runner's own.

    Loads the domain modules first (same as get_tool_providers/get_cli_providers):
    the reserved set must not depend on which modules a process happened to
    import — otherwise the same meta is accepted on a bare library connection
    and refused under the server (CB-45 review, corroborated).
    """
    _ensure_modules_loaded()
    keys = {_RESOLVER_ERRORS_KEY}
    for r in _pre_add_resolvers:
        keys |= r.meta_keys
    return frozenset(keys)


def resolver_updatable_meta_keys() -> frozenset[str]:
    """The subset of resolver-reserved keys declared repairable via update.

    Loads the modules first for the same reason as resolver_reserved_meta_keys:
    which keys are updatable must not depend on which modules a process
    imported. The runner's own `resolver_errors` is never updatable.
    """
    _ensure_modules_loaded()
    keys: set[str] = set()
    for r in _pre_add_resolvers:
        keys |= r.updatable_keys
    return frozenset(keys)


def _validate_resolver_outcome(
    outcome: dict[str, Any], resolver: PreAddResolver, forbidden: frozenset[str]
) -> None:
    """Raise unless the outcome is a JSON-serializable dict within declared keys.

    Runs INSIDE the resolver's savepoint/try so a bad outcome takes the queryable
    failure path — otherwise the later json.dumps(meta_final) in findings would
    abort the whole add with no resolver_errors stamp (CB-45 review, corroborated).
    """
    if not isinstance(outcome, dict) or any(not isinstance(k, str) for k in outcome):
        raise ValueError("resolver outcome must be a dict with string keys")
    bad = (set(outcome) - resolver.meta_keys) | (set(outcome) & forbidden)
    if bad:
        raise ValueError(f"resolver wrote undeclared/forbidden meta keys {sorted(bad)}")
    json.dumps(outcome, allow_nan=False)  # validate serializability; discard


def run_pre_add_resolvers(
    conn: sqlite3.Connection,
    observation: dict[str, Any],
    *,
    forbidden: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run every resolver against one observation; return the merged meta patch.

    NEVER-COMMIT is enforced, not documented: (1) outside an open transaction a
    bare SAVEPOINT/RELEASE would BE a commit, so the runner refuses up front —
    raise, never assert, mirroring merge.merge's ambient-transaction refusal in
    the opposite direction; (2) a resolver that closes the caller's transaction
    (commit()/ROLLBACK through the raw connection) is corruption, not an
    annotation failure — detected after every call, OUTSIDE the swallow;
    (3) cleanup is guarded so it never masks the real exception and never
    converts a swallowed annotation failure into a lost finding (db.txn's own
    cleanup lesson). Each resolver runs in SAVEPOINT sp_pre_add_<nonce>_<idx> —
    the nonce is a per-call secret so a resolver that committed cannot recreate
    the runner's savepoint by name and have the RELEASE quietly commit its
    replacement transaction; the identifier is runner-built hex, never
    resolver-supplied text (the interpolated-identifier discipline), and the
    post-RELEASE transaction check states the invariant directly rather than
    relying on name secrecy. Each resolver receives a DEEP COPY of the
    observation: the runner reads `at` for its own error stamp after resolvers
    ran, so a shared dict would let a failing resolver poison the stamp and
    abort the whole add at meta serialization — the exact failure the queryable
    stamp exists to prevent. Validated outcomes are likewise snapshotted, so a
    resolver holding a reference cannot invalidate the patch after validation.
    Failures are stamped QUERYABLY into the patch under `resolver_errors`
    (query(meta_key="resolver_errors")). `forbidden` carries the caller's own
    reserved keys, because db must not import findings.

    Loads the domain modules first, same as resolver_reserved_meta_keys and for
    the same reason: the common meta=None add path otherwise never triggers a
    load, so a bare library connection (raw sqlite3 + findings.ensure_schema)
    would silently run with an EMPTY resolver registry (Codex diff review).
    """
    _ensure_modules_loaded()
    if not conn.in_transaction:
        raise RuntimeError(
            "run_pre_add_resolvers() requires an OPEN transaction: outside one, "
            "SAVEPOINT opens a transaction and RELEASE COMMITS it — the runner "
            "would commit the resolver's writes, the inverse of the never-commits "
            "contract"
        )
    patch: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    observed_at = observation.get("at")
    nonce = secrets.token_hex(8)
    for idx, resolver in enumerate(_pre_add_resolvers):
        sp = f"sp_pre_add_{nonce}_{idx}"  # runner-built identifier, never resolver text
        conn.execute(f"SAVEPOINT {sp}")
        try:
            outcome = resolver.fn(conn, copy.deepcopy(observation))
            if not conn.in_transaction:
                raise _ResolverBrokeTransaction(resolver.name)
            if outcome is not None:
                _validate_resolver_outcome(outcome, resolver, forbidden)
                patch.update(copy.deepcopy(outcome))
            conn.execute(f"RELEASE {sp}")
            if not conn.in_transaction:
                # RELEASE ended the transaction => our savepoint had become the
                # outermost transaction opener, i.e. the caller's transaction is
                # gone and the pending INSERT would run in autocommit.
                raise _ResolverBrokeTransaction(resolver.name)
        except _ResolverBrokeTransaction:
            raise RuntimeError(
                f"pre-add resolver {resolver.name!r} closed the caller's transaction; "
                f"the pending INSERT would land outside any transaction"
            ) from None
        except Exception as e:  # noqa: BLE001
            try:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
            except sqlite3.OperationalError:
                raise RuntimeError(
                    f"pre-add resolver {resolver.name!r} corrupted the savepoint stack"
                ) from e
            sys.stderr.write(f"[pre-add resolver '{resolver.name}' failed] {e}\n")
            errors.append({"resolver": resolver.name, "error": str(e)[:500], "at": observed_at})
    if errors:
        patch[_RESOLVER_ERRORS_KEY] = errors
    return patch


# SQLITE_BUSY (5) and SQLITE_LOCKED (6). Extended codes mask down: 517 & 0xFF == 5.
_CONTENTION_CODES = frozenset({5, 6})


def is_contention(exc: BaseException) -> bool:
    """True only for SQLITE_BUSY / SQLITE_LOCKED, keyed on the numeric code.

    Never match on message text: 'cannot start a transaction within a transaction'
    and 'cannot rollback - no transaction is active' are both SQLITE_ERROR (1) and
    are programming errors that must stay loud.

    Contention is not confined to a domain module's own statements — connect()
    itself writes during schema initialization, so any command can meet it.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in _CONTENTION_CODES


@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[bool]:
    """BEGIN IMMEDIATE with isolation_level save/restore, reentrant.

    Yields True if THIS frame opened the transaction and will commit it; False if
    a transaction was already open, in which case this frame does nothing at all
    — no BEGIN, no COMMIT, no ROLLBACK — and the owning frame keeps full control.

    Never write a plain ``BEGIN`` in this codebase: it pins a read snapshot and
    the later write upgrade dies with SQLITE_BUSY_SNAPSHOT, which busy_timeout
    cannot rescue. BEGIN IMMEDIATE takes the write lock up front instead.
    """
    if conn.in_transaction:
        yield False  # ambient: the caller owns it
        return

    saved = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield True
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:  # SQLite may have auto-rolled back already
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass  # cleanup must never replace the real exception
            raise
    finally:
        conn.isolation_level = saved


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
    # KNOWN LIMIT, DELIBERATELY UNFIXED (CB-13): this basename test is exactly
    # git's own heuristic, and a `--separate-git-dir` repo whose git dir happens
    # to be named `.git` defeats it — we return the ADMIN directory rather than
    # the checkout, and bind to any `.codebugs/` beside it. There is no local
    # discriminator: git reports that directory as a valid work tree too, so any
    # "fix" here would be a different guess, not a proof. The escape hatch is a
    # declared root — see `declared_tracker_root`.
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


# --- Explicit tracker root (CB-11, CB-13) ---

ENV_ROOT = "CODEBUGS_ROOT"

SOURCE_LABELS = {
    "flag": "--tracker-root",
    "env": ENV_ROOT,
    "argument": "an explicit project directory",
    "discovery": "directory discovery",
}

_tracker_root_override: str | None = None


def set_tracker_root(root: str | None) -> None:
    """Declare this process's tracker root, overriding the upward walk.

    Entry points (`cli.main`, `server.main`) call this once, before anything
    connects. Deliberately validates NOTHING: a root may legitimately be named
    before its tracker exists, and a server that died at startup for that reason
    would lose the lazy-connect self-healing CB-11 requires. The declaration is
    checked where it is used, in `_db_path`.
    """
    global _tracker_root_override
    _tracker_root_override = root


def declared_tracker_root() -> tuple[str | None, str]:
    """Return `(root, source)` for an explicit declaration, else `(None, "discovery")`.

    Precedence is flag > environment > the cwd walk — the more deliberate the
    channel, the higher it wins. A blank value in either channel is not a
    declaration, the same convention an empty query filter follows.

    WHY A DECLARATION EXISTS AT ALL. Discovery is a heuristic, and two layouts
    prove it cannot always be right: a `--separate-git-dir` repo whose git dir is
    named `.git` binds to the admin directory (CB-13), and there is no local
    discriminator that could tell it otherwise — git itself reports that
    directory as a valid work tree. External metadata is the only thing that can
    disambiguate, so this is it.

    The returned root is absolute — see `_absolutized` for why, and for the one
    case where it cannot be (CB-49).
    """
    if _tracker_root_override and _tracker_root_override.strip():
        return _absolutized(_tracker_root_override), "flag"
    env = os.environ.get(ENV_ROOT, "")
    if env.strip():
        return _absolutized(env), "env"
    return None, "discovery"


def _absolutized(root: str) -> str:
    """Make a declared root interpretable outside this process's cwd (CB-49).

    A relative declaration resolves correctly, but it leaks verbatim into every
    diagnostic — and the MCP preflight's reader cannot know the server's cwd, so
    `tracker root ../i-b` tells them nothing. Lexical `abspath`, never
    `realpath`: a declared root is often a deliberately symlinked path, and the
    job is to pin the coordinate system, not to rewrite the declaration.
    Normalizing at read time is deliberate too — it is the same moment
    `_declared_db_path` resolves the value against cwd, so report and resolution
    cannot disagree. `abspath` on a relative value needs `os.getcwd()`, which
    raises once the cwd is deleted; fall back to the raw value so
    `describe_root`'s never-raises contract holds, and resolution then fails
    closed downstream exactly as before.
    """
    try:
        return os.path.abspath(root)
    except OSError:
        return root


def _declared_db_path(root: str, source: str) -> str:
    """Resolve a DECLARED root to its DB file, refusing rather than creating.

    Fails closed, and names the channel that supplied the value. Both halves are
    load-bearing. A declaration is ambient state that may have been exported into
    a shell days ago and inherited by an unrelated subprocess, so "no tracker
    there" must never quietly become a second, empty tracker — that is CB-8's
    original failure mode arriving through a new door. And because the value is
    ambient, the message has to say WHICH channel to go fix; without that, a
    wrong bind is a mystery with no visible cause.
    """
    label = SOURCE_LABELS[source]
    if not os.path.isdir(root):
        raise DatabaseNotFoundError(
            f"{label} names {root}, which is not a directory; "
            f"fix it, or clear it to fall back to directory discovery"
        )
    if not os.path.isdir(os.path.join(root, DB_DIR)):
        raise DatabaseNotFoundError(
            f"{label} names {root}, which has no {DB_DIR}/; "
            f"run `codebugs init {root}`, or clear {label} to fall back to "
            f"directory discovery"
        )
    # The directory is not the tracker; the database is (CB-23) — the asymmetry
    # and its benign cause are written out once, on `_resolve_db`. What matters
    # here is that accepting a directory would be the "quietly becomes a second,
    # empty tracker" this function's own docstring forbids. The check earns its
    # place by naming the channel; `mode=rw` at the open is what makes the
    # refusal race-free.
    path = os.path.join(root, DB_DIR, DB_FILE)
    if not os.path.isfile(path):
        raise DatabaseNotFoundError(
            f"{label} names {root}, whose {DB_DIR}/ holds no {DB_FILE}; "
            f"run `codebugs init {root}` to finish creating it, or clear {label} "
            f"to fall back to directory discovery"
        )
    return path


def _resolve_db(project_dir: str | None = None) -> tuple[str, bool]:
    """Locate a tracker's DB file, and say whether this route may CREATE it.

    The boolean is the whole point of returning a tuple: only the upward walk may
    create, and `connect` opens the other two routes with SQLite's `mode=rw` so
    that "must already exist" is enforced by the open itself rather than by a
    check that happened earlier (CB-23). `_db_path` is the thin wrapper for the
    callers that only want the path.

    Resolution order, most specific first: an explicit `project_dir` argument, a
    declared tracker root (`--tracker-root`, then `CODEBUGS_ROOT`), then the
    upward walk from cwd. `project_dir` outranks a declaration because it is
    per-call — `--repo <path>` names one operation's target, while a declaration
    is process-wide ambient state.

    A named `project_dir` is not an opt-in to creation: it routinely carries user
    input (`--repo <path>`), where a typo must fail loudly rather than quietly
    become a second, empty tracker. A declared root is held to the same rule.

    On those two branches a tracker means the `findings.db` FILE, not the
    `.codebugs/` directory (CB-23) — a directory alone carries no findings, so
    accepting it recreates the very failure the paragraph above forbids. The
    upward walk keeps treating the directory as the opt-in, and the asymmetry is
    deliberate: standing inside a directory is evidence about where you actually
    are, while a named or declared path is an assertion that can be stale,
    inherited by an unrelated subprocess, or simply mistyped. It also has a
    common benign cause — `init_project` creates the directory before the
    database, so a Ctrl-C'd init leaves one behind, and self-healing on the next
    command is the right answer there. `TestRefusesToAutoCreate` pins both sides.
    """
    if project_dir is not None:
        root = project_dir
        if not os.path.isdir(os.path.join(root, DB_DIR)):
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ in {root}; "
                f"check the path, or run `codebugs init {root}` to create a tracker there"
            )
        # Same rule as the declared branch below: a `.codebugs/` holding no
        # database is a half-made tracker, not an opt-in to creating one (CB-23).
        # This check exists for its MESSAGE — `mode=rw` at the open is what makes
        # the refusal race-free; a check here alone is a check-then-act.
        path = os.path.join(root, DB_DIR, DB_FILE)
        if not os.path.isfile(path):
            raise DatabaseNotFoundError(
                f"{DB_DIR}/ in {root} holds no {DB_FILE}; "
                f"check the path, or run `codebugs init {root}` to finish creating it"
            )
        return path, False

    declared, source = declared_tracker_root()
    if declared is not None:
        return _declared_db_path(declared, source), False

    # Read cwd ONCE, and treat losing it as "no tracker" rather than letting an
    # OSError escape. A deleted working directory is not hypothetical here: a
    # long-lived MCP server outlives the git worktree it was started in, and
    # `os.getcwd()` then raises FileNotFoundError. Escaping, that would bypass
    # every DatabaseNotFoundError handler — a traceback in the CLI, and a FATAL
    # startup preflight in the server, which is the one thing CB-11 forbids.
    try:
        cwd = os.getcwd()
    except OSError as e:
        raise DatabaseNotFoundError(
            f"cannot determine the current directory ({e}) — it may have been deleted; "
            f"cd somewhere that exists, or name a tracker with --tracker-root"
        ) from e

    root = _find_db_root(cwd)
    if root is None:
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
    # The one route allowed to create: see the asymmetry note above.
    return os.path.join(root, DB_DIR, DB_FILE), True


def _db_path(project_dir: str | None = None) -> str:
    """The resolved DB path, without the may-create flag. See `_resolve_db`."""
    return _resolve_db(project_dir)[0]


def describe_root() -> dict[str, Any]:
    """Report where this process is bound and which channel decided it.

    NEVER RAISES. It is the shared body of `codebugs where` and the MCP startup
    preflight, and the preflight must be warn-only: a fatal one would kill a
    server whose project only appears later, which is the self-healing property
    CB-11's card explicitly protects.

    One resolver, two consumers, so the diagnostic and the server can never
    disagree about where the process is pointed.
    """
    declared, source = declared_tracker_root()
    try:
        path = _db_path()
    except (DatabaseNotFoundError, OSError) as e:
        # OSError as well, so "never raises" is true rather than aspirational:
        # the filesystem can fail underneath any of this, and a preflight that
        # dies is a fatal preflight no matter which exception killed it.
        return {
            "root": declared,
            "source": source,
            "source_label": SOURCE_LABELS[source],
            "path": None,
            "exists": False,
            "error": str(e),
        }
    # `path` is always `<root>/<DB_DIR>/<DB_FILE>`, so the root is recoverable
    # without walking again — and cannot disagree with the path we just resolved.
    #
    # `exists` is reported separately because resolving is not the same as being
    # there: on the walk route a `.codebugs/` holding no database resolves fine
    # and the next command CREATES the tracker (CB-23). Without this the
    # diagnostic prints a path that is not there and calls it healthy — which is
    # exactly the CB-13 misbinding's shape, where the wrong root is a stray
    # directory. A binding you cannot see is a binding you cannot debug (CB-11).
    return {
        "root": os.path.dirname(os.path.dirname(path)),
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "path": path,
        "exists": os.path.isfile(path),
        "error": None,
    }


def init_project(project_dir: str | None = None, *, force: bool = False) -> dict[str, Any]:
    """Create a `.codebugs/` tracker in `project_dir` (default cwd).

    **The only function that creates the `.codebugs/` DIRECTORY** — the single
    `os.makedirs` in the package — so opting a project in is always a deliberate
    act. State it that way rather than "the only function that creates a
    tracker", which stopped being true once the distinction mattered: the upward
    walk in `connect` will fill in a `findings.db` inside a directory that
    already exists, and the named and declared routes refuse to do even that
    (CB-23). Idempotent — an existing tracker here is left alone.

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
    # `_open(create=True)`, not `connect`: this is the one place a database may be
    # brought into existence at a named path, and `connect` refuses a named root
    # that holds none (CB-23). Note the directory is created first, so an
    # interruption here leaves a `.codebugs/` with no database — the state the
    # upward walk deliberately still self-heals, and the named/declared branches
    # deliberately refuse.
    _open(path, create=True).close()
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
        from codebugs import (  # noqa: F401
            bench,
            blockers,
            claims,
            findings,
            merge,
            milestones,
            provenance,
            reqs,
            similarity,
            sweep,
        )

        _modules_loaded = True


def _open(path: str, *, create: bool) -> sqlite3.Connection:
    """Open a connection at an EXACT path and apply every module's schema.

    Split out of `connect` so `init_project` can reach it without going through
    `_resolve_db`, whose job on the named and declared branches is to refuse a
    path that does not already hold a database (CB-23). Before the split, init
    created its database *by way of* `connect`, so tightening the resolver broke
    the one caller that is supposed to create.

    Two callers pass ``create=True`` and the difference between them is the whole
    design: `init_project`, which has just made the `.codebugs/` directory, and
    `connect` on the upward-walk route, where the directory was already there.
    Neither invents a directory — that is `init_project`'s single `os.makedirs`,
    and it is the precise sense in which opting a project in is deliberate.
    `TestOpenCallSitesRatchet` pins that there are exactly these two.

    ``create=False`` opens through SQLite's ``mode=rw`` URI, which fails rather
    than creating. That is what makes the refusal race-free: the resolver's
    ``isfile`` check runs earlier and only supplies a good message, so on its own
    it is a check-then-act — another process removing the database in between
    would get a fresh empty one built here, which is precisely CB-23's failure
    mode. Enforcing existence at the open closes the window.
    """
    if not create:
        # pathname2url escapes `?` and `#`, which would otherwise be read as URI
        # syntax; abspath because a relative path has no valid file: URI.
        uri = "file:" + pathname2url(os.path.abspath(path)) + "?mode=rw"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as e:
            # Keep the documented exception type: callers catch
            # DatabaseNotFoundError, and a bare OperationalError here would reach
            # the CLI's contention arm and be misreported as "database busy".
            if is_contention(e):
                raise
            raise DatabaseNotFoundError(
                f"no readable {DB_FILE} at {path} ({e}); "
                f"run `codebugs init` for that project, or check the path"
            ) from e
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Explicit; was inherited from sqlite3.connect(timeout=5.0)'s default. This is
    # what turns a losing writer into a clean rowcount=0 instead of an exception.
    conn.execute("PRAGMA busy_timeout=5000")

    _ensure_modules_loaded()
    for entry in _resolved_order():
        entry.ensure_fn(conn)

    return conn


def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open the codebugs database.

    Creates one only on the upward-walk route, and only inside a `.codebugs/`
    directory that already exists — the deliberate asymmetry documented on
    `_resolve_db` (CB-23). A named `project_dir` or a declared root is opened
    `mode=rw` and never creates anything.
    """
    path, may_create = _resolve_db(project_dir)
    return _open(path, create=may_create)
