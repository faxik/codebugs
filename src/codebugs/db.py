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
import stat
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


# --- Read-enricher seam (BT-7 Т-56) ---


@dataclass(frozen=True)
class ReadEnricher:
    """A registered read enricher: annotates rows a domain read is about to return.

    A member of this file's registry family and deliberately the first READING
    one, so half the older contract does not apply and copying it would be
    cargo. It writes nothing, so there is no transaction to be inside, no
    savepoint, no nonce and no never-commit rule to enforce. No ordinal is
    written here on purpose — a count in prose is one this repository has been
    wrong about every time it left one there. What it does share
    with `PreAddResolver` is the shape that keeps core ignorant of an
    extension's vocabulary: `key` is DECLARED at registration exactly as
    `meta_keys` is, so the runner can stamp a failure under the extension's own
    name without ever hardcoding it.
    """

    name: str
    fn: Callable[..., None]
    key: str
    fallback: Callable[[str | None], dict[str, Any]] | None


_read_enrichers: list[ReadEnricher] = []


def _bare_unavailable(error: str | None) -> dict[str, Any]:
    """The last-resort failure stamp for an enricher that declared no shape."""
    return {"state": "unavailable", "error": error}


def register_read_enricher(
    name: str,
    fn: Callable[..., None],
    *,
    key: str,
    fallback: Callable[[str | None], dict[str, Any]] | None = None,
) -> None:
    """Register an enricher for the ordinary finding read paths.

    `fn(conn, rows, *, resolve, project_dir)` annotates `rows` IN PLACE, writing
    its summary under `key` on every row. `resolve` is the caller's permission
    to spend real work (git, network, anything measurable); a `False` there must
    still produce a summary, only a cheaper one — the cost asymmetry between
    `get` and `query` is the caller's decision to make, not the enricher's.

    `fallback(error)` builds this enricher's summary for a row it could not
    answer for, and it is DECLARED here for the same reason `key` is: the runner
    must be able to stamp a failure in the extension's own vocabulary without
    knowing a single one of its field names. Omitting it gets a two-key
    `{"state": "unavailable", "error": …}`, which is honest but is a DIFFERENT
    SHAPE from whatever the enricher normally writes — and review measured what
    that costs: every consumer written against the documented summary raises
    `KeyError` on exactly the path this seam exists to make survivable. An
    enricher whose summary has a fixed key set should supply one.

    Same name-keyed discipline as its sibling registries so module re-import
    is a no-op, and the same refusal on a same-name re-registration that changes
    the CONTRACT (`fn` or `key`): a silently ignored implementation that never
    runs while its author believes it registered is CB-15's failure shape.
    """
    for existing in _read_enrichers:
        if existing.name == name:
            if existing.fn is not fn or existing.key != key or existing.fallback is not fallback:
                raise ValueError(
                    f"read enricher {name!r} already registered with a different "
                    f"function or key; a silently ignored implementation would never run"
                )
            return
    for existing in _read_enrichers:
        if existing.key == key:
            raise ValueError(
                f"read-enricher key {key!r} already declared by enricher {existing.name!r}"
            )
    _read_enrichers.append(ReadEnricher(name, fn, key, fallback))


def run_read_enrichers(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    resolve: bool,
    project_dir: str | None = None,
) -> None:
    """Annotate `rows` in place with every registered enricher's summary.

    NEVER RAISES. An extension that fails while enriching must not take down the
    read it was decorating — the caller asked for a finding, and the finding is
    still there.

    **The failure is VISIBLE, and that is a decision rather than an inheritance.**
    The older seams swallow-and-log to stderr, which is nothing at all to an MCP
    caller; worse, here a silently missing key would be indistinguishable from
    "this row carries no anchor", and telling those two apart is the entire
    reason this seam exists. So the runner GUARANTEES the enricher's declared
    key is present on every row after the pass, and fills any it did not get
    through the enricher's own declared `fallback`, so the failure stamp has the
    same SHAPE as a real summary rather than being a second, narrower object a
    consumer must special-case. stderr keeps its line as the immediate channel;
    the response is the durable one.

    `_ensure_modules_loaded()` first, for the pre-add resolver's reason: which
    extensions are registered must not depend on which modules this process
    happened to import.
    """
    _ensure_modules_loaded()
    for enricher in _read_enrichers:
        error: str | None = None
        try:
            enricher.fn(conn, rows, resolve=resolve, project_dir=project_dir)
        except Exception as e:  # noqa: BLE001 — a read must survive its decoration
            error = f"{type(e).__name__}: {e}"[:500]
            sys.stderr.write(f"[read enricher '{enricher.name}' failed] {e}\n")
        build = enricher.fallback or _bare_unavailable
        for row in rows:
            if enricher.key in row:
                continue
            reason = error or "enricher produced no summary for this row"
            try:
                row[enricher.key] = build(reason)
            except Exception as e:  # noqa: BLE001 — the guard may not have a guard
                sys.stderr.write(f"[read enricher '{enricher.name}' fallback failed] {e}\n")
                row[enricher.key] = _bare_unavailable(reason)


def connection_root(conn: sqlite3.Connection) -> str | None:
    """The project directory this CONNECTION's tracker lives in, or None.

    A read path that wants to resolve something against the repository needs a
    root, and the three obvious sources are all wrong for it. Ambient cwd is
    refused in capitals by BT-7 Р3 — a long-lived server outlives the directory
    it started in. `describe_root()` is deliberately a one-resolver/two-consumer
    diagnostic, and it answers "where would a connection be opened from", not
    "where did THIS one come from". And a required argument would make the
    ordinary `get` an unusable one.

    NEVER RAISES, for the same reason `describe_root` does not: it is consulted
    on the ordinary read path and a diagnostic that can take down the thing it
    describes is worse than no diagnostic.

    So the coordinate is taken from the connection the caller already handed us:
    the main database file's own path, whose grandparent is the project
    directory by construction (`<root>/.codebugs/findings.db`). None for an
    in-memory or otherwise pathless database, and None is a refusal the caller
    must handle — never a licence to fall back to cwd.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
        path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
        if name != "main":
            continue
        if not isinstance(path, str) or not path:
            return None
        # `abspath` on a RELATIVE value needs `os.getcwd()`, which raises once
        # the cwd has been deleted out from under a long-lived server — the same
        # hazard `_absolutized` documents, and it matters more here because this
        # function is called on the ordinary read path, OUTSIDE the enricher's
        # own failure guard. A caller that cannot be told where it is gets
        # `None`, which every consumer already treats as "no root".
        try:
            parent = os.path.dirname(os.path.abspath(path))
        except OSError:
            return None
        if os.path.basename(parent) != DB_DIR:
            return None
        return os.path.dirname(parent)
    return None


# SQLITE_BUSY (5) and SQLITE_LOCKED (6). Extended codes mask down: 517 & 0xFF == 5.
_CONTENTION_CODES = frozenset({5, 6})

# CB-86. Environmental: the tracker exists and the ENVIRONMENT refuses the write.
#   8  SQLITE_READONLY  — measured (read-only dir gives extended 1544 & 0xFF == 8;
#                         read-only file gives a plain 8)
#   14 SQLITE_CANTOPEN  — measured (chmod 000)
#   10 SQLITE_IOERR     — reasoned from SQLite's documentation, NOT reproduced here
#   13 SQLITE_FULL      — reasoned from SQLite's documentation, NOT reproduced here
#
# Two entries are deliberately ABSENT, and both absences were earned by measuring
# rather than by reading a card. SQLITE_PERM (3) never occurs on any CLI-reachable
# path — `chmod 000` yields 14 — so listing it would be a dead entry. SQLITE_NOTADB
# (26) arrives as `sqlite3.DatabaseError`, NOT `OperationalError`, so it could never
# reach the arm that consults this set.
#
# AN UNLISTED CODE FALLS THROUGH TO TODAY'S BEHAVIOUR — a traceback. This is an
# enumeration, and this repo has been bitten by enumerations six times; the point
# is that this one fails toward the status quo instead of toward a wrong answer.
_ENVIRONMENTAL_CODES = frozenset({8, 10, 13, 14})


def is_contention(exc: BaseException) -> bool:
    """True only for SQLITE_BUSY / SQLITE_LOCKED, keyed on the numeric code.

    Never match on message text: 'cannot start a transaction within a transaction'
    and 'cannot rollback - no transaction is active' are both SQLITE_ERROR (1) and
    are programming errors that must stay loud.

    Contention is not confined to a domain module's own statements, but the
    reason is narrower than it was: this paragraph used to say `connect()` itself
    writes during schema initialization, full stop, and CB-195 ended that. The
    seed inserts now run only when their row is missing, so on any tracker that
    has been opened before, `connect()` takes no write lock and cannot contend.
    The FIRST open of a tracker still does write, and there any command — a pure
    read included — can meet contention before reaching its own statements.
    """
    return _sqlite_code_in(exc, _CONTENTION_CODES)


def _sqlite_code_in(exc: BaseException, codes: frozenset[int]) -> bool:
    """Shared extraction for the code classifiers: `getattr`, mask, membership.

    The two classifiers are separate POLICIES on purpose (see `_is_environmental`),
    but the mechanism is one thing, and this repo has a standing habit of growing a
    third copy of any boilerplate it leaves duplicated twice.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in codes


def _is_environmental(exc: BaseException) -> bool:
    """True for a failure about the ENVIRONMENT, not about this package (CB-86).

    Read-only mount, read-only file, unwritable directory, full disk, I/O error on
    network storage. Keyed on the numeric code with the same `& 0xFF` mask
    `is_contention` uses — and the mask is load-bearing here too, because a
    read-only *directory* raises the EXTENDED code 1544
    (`SQLITE_READONLY_DIRECTORY`), which is 8 once masked. Measured.

    DELIBERATELY SEPARATE FROM `is_contention` rather than folded into it. That
    set matches {5, 6} because a contended write must stay retryable and
    distinguishable — `claims.py`'s `undetermined` outcome tells the caller to
    re-issue the identical call. Widening it would tell a caller to retry a full
    disk forever, which is the opposite of what either layer needs.

    PRIVATE, AND THAT IS THE POINT — unlike its public sibling `is_contention`,
    which `cli.main` must call. This function exists so `_open` can raise a TYPE;
    a caller at the CLI boundary is precisely the design that was rejected, and
    exporting the classifier would make that rejected design a two-line patch
    looking like an obvious tidy-up:

        except sqlite3.OperationalError as e:
            if db.is_environmental(e):      # <- this
                print(...); sys.exit(1)

    That patch silently deletes the discriminator `tests/test_bench.py:789` exists
    to protect (a post-commit failure must keep its traceback), and prose cannot
    refuse it. `tests/test_db_unwritable.py::TestTheClassifierStaysInsideDb` pins
    that nothing outside this module names it.
    """
    return _sqlite_code_in(exc, _ENVIRONMENTAL_CODES)


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
            encoding="utf-8",
            # Not every `rev-parse` answer is hex. `--show-toplevel` prints a
            # PATH — `provenance._repo_root` is the caller — and a repository
            # whose root directory name is not valid UTF-8 made strict decoding
            # raise `UnicodeDecodeError`. That is a `ValueError`, so the tuple
            # below does not catch it and it escaped a helper whose whole
            # contract is "returns None if git is unavailable", taking a whole
            # `check_findings` batch down as a traceback. Reproduced.
            #
            # Strict widening: a hex SHA decodes identically either way, so no
            # existing caller changes. Callers that put the result in a
            # user-visible string must still sanitize it — `provenance._verdict`
            # is the worked example, because a surrogate cannot be re-encoded
            # to UTF-8 and MCP serializes these dicts to JSON.
            errors="surrogateescape",
            timeout=10,
            stderr=subprocess.DEVNULL if silent else None,
            cwd=cwd,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        # OSError, not FileNotFoundError (CB-79). The narrow spelling covered
        # "git is missing" and nothing else, so a git binary that exists but is
        # not executable raised PermissionError straight through a caller that
        # had asked to be told "unavailable" — reproduced with a chmod-000 git
        # as the only one on PATH. NotADirectoryError from a deleted `cwd` is
        # the same class. `subprocess.SubprocessError` is NOT an OSError
        # subclass, so it has to stay: dropping it loses CalledProcessError and
        # TimeoutExpired, which is most of what this guard is for.
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


# Places the upward walk could not interrogate, each as `(path, reason)` — the
# same `(what, why)` shape `_path_state` itself returns, so the two cannot drift
# into different spellings of one answer. Built by `_walk_db_root`; see there for
# why the walk records instead of stopping.
Unexamined = tuple[tuple[str, str], ...]


class DatabaseNotFoundError(RuntimeError):
    """No `.codebugs/` tracker was found, and one must not be created implicitly.

    Auto-creating is what makes a wrong-directory bind silent: the caller gets an
    empty DB instead of an error, and every finding written into it is invisible
    to everyone else. `init_project()` is the only function that may create one.

    Carries `unexamined` (CB-218) so the walk's skipped candidates survive the
    raise. Without it `describe_root`'s error branch would have to report an
    EMPTY list for a walk that really did skip something — and an empty list
    there means *the walk ran and skipped nothing*, so the key would lie in the
    one state it exists to describe. Empty on every route that did not walk.
    """

    def __init__(self, message: str, *, unexamined: Unexamined = ()) -> None:
        super().__init__(message)
        self.unexamined = unexamined


class TrackerUnwritableError(RuntimeError):
    """The tracker is there, and this process cannot open it for writing (CB-86).

    A SIBLING of `DatabaseNotFoundError`, not a subclass, because the two mean
    opposite things to the person reading the message: *there is no tracker here,
    make one* versus *the tracker exists and your permissions or your disk are the
    problem*. Before this, a read-only database on the named route was reported as
    the former — "run `codebugs init` for that project" — which is advice that
    cannot work and would create nothing.

    RAISED ONLY FROM `_open`, and that is the whole design (CB-86 ratified
    2026-08-19 by adversarial review). The rejected alternative was classifying
    `sqlite3.OperationalError` at the `cli.main` boundary, which cannot tell a
    pre-write failure from a post-commit one — the constraint CB-55 states, and
    which `tests/test_bench.py:789` enforces by asserting that a post-commit
    failure keeps its traceback. Raising a TYPE from `_open` makes the provenance
    structural instead of argued: `_open` raises before it returns a connection.

    THE PRECISE CLAIM, because over-claiming here is how the boundary design
    failed: this means *the failure happened while opening a connection*. It does
    not by itself prove nothing was written earlier in the process through a
    different connection. No handler connects twice today (measured), but that is
    a property of the call sites, not of this type.
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

    Walks up on the same rules as `_walk_db_root` so the two agree on where a
    repository begins: a `.git` DIRECTORY is a normal checkout and ends the
    search, a `.git` FILE is a worktree only if it carries a `commondir`.

    The probe is `_path_state` for CB-218's reason, and BEHAVIOUR HERE IS
    UNCHANGED — said plainly rather than implied, because a claim of coverage
    that no test can discriminate is the shape this direction exists to close.
    `is_dir()` and `is_file()` both answer False on a path that could not be
    looked at, and both also answer False when the answer is a genuine
    *something else*; either way this loop walks on. `_path_state` folds the two
    stats into one and makes the undetermined case NAMED rather than inferred,
    so the next reader cannot restore a two-valued read by accident. It records
    nothing: this function only chooses which of two refusal sentences
    `_resolve_db` prints, and it is reached only when `_walk_db_root` already
    returned None — having walked the same prefix under the same rules, and
    having already recorded every undetermined `.git` on it.
    """
    cur = Path(start).resolve()
    while True:
        git = cur / DOT_GIT
        kind, _detail = _path_state(str(git))
        if kind == PATH_DIR:
            return None
        if kind == PATH_FILE:
            return str(cur) if _linked_worktree_gitdir(git) is not None else None
        if cur.parent == cur:
            return None
        cur = cur.parent


def _walk_db_root(start: str | None = None) -> tuple[str | None, Unexamined]:
    """Walk up from `start` (default cwd) for a `.codebugs/`, and say what it could not see.

    Returns `(root, unexamined)`: the directory containing `.codebugs/` (or None
    if walking hits a repo boundary or the filesystem root), and every place on
    the route whose question could not be answered.

    THE ALGORITHM IS UNCHANGED — deliberately, to the letter (CB-218). A
    `.git/` DIRECTORY is a repo root and stops the walk (picking the enclosing
    repo's DB when invoked inside a submodule would be worse than refusing). A
    `.git` FILE is ambiguous: in a linked worktree it points at the main repo, so
    discovery continues from there; in a submodule it stays a boundary. And
    `.codebugs/` still outranks the boundary within one directory. What changed
    is the PRIMITIVE the three questions are asked with, and what happens to an
    answer that never arrived.

    WHY THIS FUNCTION EXISTS AT ALL, since CB-203 already made five sites
    three-valued. Those five look at an ALREADY-FOUND root. This one decides
    WHICH root will be found, and it runs BEFORE all of them, so a two-valued
    read here is not a worse message — it is a different tracker. Measured on the
    unfixed tree: with the execute bit off an intermediate directory that HOLDS
    the project's tracker, and an unrelated `.codebugs/` one level above it,
    `codebugs where` reported a completely clean binding to the stranger at exit
    0, no warning anywhere, and `stats` then answered with the stranger's empty
    population. Not a lost warning and not a false promise — a silent bind to the
    wrong tracker, which is what CB-8 was filed to prevent and what CB-11 built
    visibility for. `Path.is_dir()` swallows the underlying `OSError` exactly as
    `os.path.isdir` does, so *could not look* arrived here spelled *not there*.

    ALL THREE PROBES, not just the one the card named. The `.git` questions are
    the same predicate about a different fact: an undetermined answer about
    `cur/.git` reads as *no boundary here*, and the walk then continues PAST the
    repository boundary and binds to whatever sits beyond it — the same harm
    through a second door. Measured, and by two independent routes: with the
    execute bit off the repository directory, and — with that directory fully
    readable, so the `.git` probe is the only undetermined one — with `.git` a
    symbolic-link loop. Both walked past the boundary onto a stranger's tracker
    at exit 0. Fixing one probe of three would be this repository's own
    recurring defect: validating elements instead of their composition.

    AN UNDETERMINED ANSWER RECORDS AND WALKS ON; IT DOES NOT STOP. The cost of
    each direction decides it. `_path_state` answers *undetermined* for every
    filesystem failure, not only for a withheld execute bit — an I/O error, a
    symlink loop, a name past the length limit, a network mount answering with an
    errno nobody here enumerated. One of those on an UNRELATED ancestor between
    the caller and their real tracker is harmless today; stopping would turn it
    into a refusal to work at all. This tree treats a false refusal as the worse
    outcome, and rightly. Continuing is safe; STAYING SILENT about the skipped
    candidate is not, because silence is the entire mechanism by which the
    measured state above became a wrong bind rather than a visible one.

    The list is ordered as the walk met them and is not deduplicated: one
    directory can contribute two entries (its `.codebugs/` and its `.git`), and
    that is two questions genuinely left unanswered, not one repeated.
    """
    cur = Path(start or os.getcwd()).resolve()
    seen: set[Path] = set()
    unexamined: list[tuple[str, str]] = []
    while cur not in seen:
        seen.add(cur)
        tracker = cur / DB_DIR
        kind, detail = _path_state(str(tracker))
        if kind == PATH_DIR:
            return str(cur), tuple(unexamined)
        if kind is None:
            unexamined.append((str(tracker), detail or "could not look at it"))
        git = cur / DOT_GIT
        kind, detail = _path_state(str(git))
        if kind == PATH_DIR:
            return None, tuple(unexamined)
        if kind == PATH_FILE:
            main_root = _worktree_main_root(git)
            if main_root is None:
                return None, tuple(unexamined)
            cur = Path(main_root)
            continue
        if kind is None:
            unexamined.append((str(git), detail or "could not look at it"))
        if cur.parent == cur:
            return None, tuple(unexamined)
        cur = cur.parent
    return None, tuple(unexamined)


def _find_db_root(start: str | None = None) -> str | None:
    """The walked root, without the unexamined list. See `_walk_db_root`.

    The same thin-wrapper shape `_db_path` has over `_resolve_db`, and for the
    same reason: several callers want only the root, and two of them —
    `init_project`'s shadow guard and `cli`'s `--force` variant of it — ask a
    question the list cannot help with. Keeping this signature is also what lets
    the walk's existing tests stay untouched, which is worth something on a
    change whose whole claim is that the algorithm did not move.

    KNOWN AND DELIBERATELY OUT OF SCOPE: those two shadow guards still read a
    None root as *no enclosing tracker*, so an unexaminable ancestor lets `init`
    create a tracker that a real one above would shadow. That is the same
    two-valued read one level out, in a different decision, and it needs its own
    answer (refuse? warn?) rather than a silent inheritance of this one's.
    """
    return _walk_db_root(start)[0]


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


# Nouns for the things that can sit at a path and are not a regular file or a
# directory. Used only to build a diagnostic sentence, so an unrecognised mode
# degrades to a generic word rather than to a wrong one.
_NODE_NOUNS: dict[int, str] = {
    stat.S_IFCHR: "a character device",
    stat.S_IFBLK: "a block device",
    stat.S_IFIFO: "a named pipe",
    stat.S_IFSOCK: "a socket",
}

# The four answers `_path_state` can give. `absent` is the only one that
# licenses a claim of absence; see the function's docstring.
PATH_FILE = "file"
PATH_DIR = "dir"
PATH_OTHER = "other"
PATH_ABSENT = "absent"


def _path_state(path: str) -> tuple[str | None, str | None]:
    """What is at `path` — four answers, or `None` meaning *could not tell* (CB-203).

    `os.path.isfile`/`os.path.isdir` answer a three-valued question with two
    values: they return False for *nothing is there* and for *the question could
    not be answered*, because they swallow every `OSError` the underlying stat
    raises. A caller then reads False as proof of absence and says so out loud.
    Measured, and this is the whole of CB-203: with the execute bit removed from
    a `.codebugs/` directory (`chmod 666`), `codebugs where` printed "no database
    there yet — the next command creates one" at exit 0, over a tracker that was
    sitting right there and that every other verb refused to open.

    THIS TREE ALREADY RATIFIED THE SAME FIX ONE FILE OVER. `provenance.py`
    swapped `os.path.isfile` for a stat-with-arms guard for CB-85, on the
    identical reasoning — "a positive claim about the file derived from a
    question that was never answered". `db.py` kept the two-valued spelling at
    five call sites, which is this repository's most-repeated shape: a rule fixed
    at the sites someone enumerated, while the population is larger than the
    list.

    Returns `(kind, detail)`:

    - `("file", None)`   — a regular file is there. Affirmative proof.
    - `("dir", None)`    — a directory is there. Affirmative proof.
    - `("other", noun)`  — something else is there (a named pipe, a socket, a
      device). Affirmative proof that it is not a file and not a directory,
      and `noun` names it for the message.
    - `("absent", None)` — affirmative proof that NOTHING is at that name, and
      that the failure to find it was `ENOENT` rather than an inability to look.
    - `(None, why)`      — undetermined. `why` is a short human sentence.

    THE ORDER OF THE TWO STATS IS LOAD-BEARING, not tidiness. `lstat` comes
    first because a DANGLING SYMLINK is exactly the state that must not read as
    `absent`: `os.stat` on one raises `FileNotFoundError`, indistinguishable
    from an empty name, and the two states differ in what happens next — the
    next command creates a database in the empty case and, in the symlink case,
    creates one somewhere else entirely or fails. CB-203's card records this as
    its "related variety", found by the same run.

    `NotADirectoryError` deliberately does NOT read as `absent`, and that is a
    narrowing against `provenance.py`'s version of this guard, which folds it in
    with `FileNotFoundError`. There the caller asks *is this file still there*,
    and a non-directory in the path proves it is not. Here the caller asks *is
    this the empty slot inside a real tracker where the next command will create
    a database* — and a non-directory in the path proves it is NOT that state
    either. One guard, two callers, two different questions: the fail-closed
    answer is the only one both can use.

    `ValueError` is caught because it is the one exception `os.lstat` raises
    that is not an `OSError` — a NUL byte in the path — and because
    `describe_root` may never raise (CB-11). `os.path.isfile` swallowed it for
    free; a stat-based guard has to say so.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        # ENOENT on the name itself: nothing is there, and we could look.
        return PATH_ABSENT, None
    except (OSError, ValueError) as e:
        return None, f"could not look at it ({_why(e)})"
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(path)
        except (OSError, ValueError) as e:
            return None, f"a symbolic link is there and does not resolve ({_why(e)})"
    if stat.S_ISREG(st.st_mode):
        return PATH_FILE, None
    if stat.S_ISDIR(st.st_mode):
        return PATH_DIR, None
    return PATH_OTHER, _NODE_NOUNS.get(stat.S_IFMT(st.st_mode), "something that is not a file")


def _why(exc: BaseException) -> str:
    """The shortest honest description of a failed stat, for a diagnostic line.

    `strerror` when the OS supplied one — "Permission denied" is what the reader
    needs and `str(exc)` would bury it behind an errno and the path already
    printed on the line above. A `ValueError` has no `strerror`, so it falls
    back to its own text.
    """
    strerror = getattr(exc, "strerror", None)
    return strerror or str(exc) or type(exc).__name__


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
    # Each of the three checks below refuses on the same rule: it may assert
    # what is (or is not) at a path only when it could actually look. An
    # undetermined answer still REFUSES — the whole branch is fail-closed by
    # design — but it says *could not determine* rather than inventing an
    # absence, which is CB-203's rule applied where the same predicate lives.
    # Fixing it only in `describe_root` and leaving these two behind is the
    # enumeration failure this repository pays for most often.
    kind, detail = _path_state(root)
    if kind is None:
        raise DatabaseNotFoundError(
            f"{label} names {root}, and whether a tracker is there could not be "
            f"determined — {detail}; fix the path or its permissions, or clear "
            f"{label} to fall back to directory discovery"
        )
    if kind != PATH_DIR:
        raise DatabaseNotFoundError(
            f"{label} names {root}, which is not a directory; "
            f"fix it, or clear it to fall back to directory discovery"
        )
    kind, detail = _path_state(os.path.join(root, DB_DIR))
    if kind is None:
        raise DatabaseNotFoundError(
            f"{label} names {root}, and whether it has a {DB_DIR}/ could not be "
            f"determined — {detail}; fix the path or its permissions, or clear "
            f"{label} to fall back to directory discovery"
        )
    if kind != PATH_DIR:
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
    kind, detail = _path_state(path)
    if kind is None:
        raise DatabaseNotFoundError(
            f"{label} names {root}, and whether its {DB_DIR}/ holds a {DB_FILE} "
            f"could not be determined — {detail}; fix the path or its "
            f"permissions, or clear {label} to fall back to directory discovery"
        )
    if kind == PATH_DIR or kind == PATH_OTHER:
        # Something IS at that name and it is not a database. Saying "holds no
        # findings.db" here would be the same untruth in a quieter place: the
        # operator would go and create what is already in the way.
        what = "a directory" if kind == PATH_DIR else detail
        raise DatabaseNotFoundError(
            f"{label} names {root}, whose {DB_DIR}/ has {what} where {DB_FILE} "
            f"should be; move it aside, or clear {label} to fall back to "
            f"directory discovery"
        )
    if kind != PATH_FILE:
        raise DatabaseNotFoundError(
            f"{label} names {root}, whose {DB_DIR}/ holds no {DB_FILE}; "
            f"run `codebugs init {root}` to finish creating it, or clear {label} "
            f"to fall back to directory discovery"
        )
    return path


def unexamined_phrases(unexamined: Unexamined) -> tuple[str, ...]:
    """`(path, reason)` pairs as human phrases — one definition, three consumers.

    The refusal message, `codebugs where` and the MCP preflight all say this,
    each in its own frame, so the wording lives here rather than three times
    over. Returns `()` on an empty list, which is what makes every caller's "say
    something only when there is something to say" a single `for` or a single
    `if` rather than a rule to re-establish.

    NOTHING IS TRUNCATED, and a truncating version was written first and then
    REFUTED BY MEASUREMENT rather than by argument. One withheld execute bit
    makes every question BELOW it unanswerable too, since the walk asks with
    absolute paths and each of them traverses the wall: a tracker three
    directories down yields six entries for one condition with one fix, and
    those six read as noise. The cap kept the first three, in walk order — and
    walk order runs DEEPEST FIRST, so what it kept were the wall's SHADOWS
    (`…/b/c/.codebugs`, which never existed) while the one entry naming the
    wall itself, and the directory actually holding the user's tracker, fell off
    the end. A short diagnostic missing its own cause is worse than a long one.
    Length is bounded by how deep the caller stood below the wall, and this
    state is rare and serious enough to be worth every line of it.
    """
    return tuple(f"{path} ({why})" for path, why in unexamined)


def _unexamined_caveat(unexamined: Unexamined) -> str:
    """The 'and here is what I could not look at' half of a refusal, or nothing.

    Empty string on an empty list, so a refusal about a route that examined
    everything stays BYTE-IDENTICAL to what it has always said. That is the same
    discipline the two consumers follow when they print: say something only when
    there is something to say. The key that carries this to a caller is
    unconditional; the SENTENCE is not, because a sentence saying "nothing was
    skipped" is noise on every healthy tracker in existence.
    """
    phrases = unexamined_phrases(unexamined)
    if not phrases:
        return ""
    noun = "place" if len(unexamined) == 1 else "places"
    return (
        f" — {len(unexamined)} {noun} on the way up could not be examined "
        f"({'; '.join(phrases)}), so this is not proof that no tracker is there"
    )


def _resolve_db(project_dir: str | None = None) -> tuple[str, bool, Unexamined]:
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

    THE THIRD ELEMENT is the walk's unexamined candidates (CB-218), passed
    through unchanged for `describe_root` to report. It is `()` on the named and
    declared branches BECAUSE NO WALK HAPPENED THERE, and that is a fact rather
    than a default: those two routes ask about one path they were given. An empty
    value therefore always means *the route ran and skipped nothing*, never *no
    such channel* — the discipline `attention`, `stripped_meta_keys` and
    `exists_reason` already follow here. `_db_path` still returns only the path,
    so none of its callers move.
    """
    if project_dir is not None:
        root = project_dir
        kind, detail = _path_state(os.path.join(root, DB_DIR))
        if kind is None:
            raise DatabaseNotFoundError(
                f"whether {root} has a {DB_DIR}/ could not be determined — {detail}; "
                f"check the path and its permissions"
            )
        if kind != PATH_DIR:
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ in {root}; "
                f"check the path, or run `codebugs init {root}` to create a tracker there"
            )
        # Same rule as the declared branch below: a `.codebugs/` holding no
        # database is a half-made tracker, not an opt-in to creating one (CB-23).
        # This check exists for its MESSAGE — `mode=rw` at the open is what makes
        # the refusal race-free; a check here alone is a check-then-act.
        path = os.path.join(root, DB_DIR, DB_FILE)
        kind, detail = _path_state(path)
        if kind is None:
            raise DatabaseNotFoundError(
                f"whether {DB_DIR}/ in {root} holds a {DB_FILE} could not be "
                f"determined — {detail}; check the path and its permissions"
            )
        if kind == PATH_DIR or kind == PATH_OTHER:
            what = "a directory" if kind == PATH_DIR else detail
            raise DatabaseNotFoundError(
                f"{DB_DIR}/ in {root} has {what} where {DB_FILE} should be; "
                f"move it aside, or check the path"
            )
        if kind != PATH_FILE:
            raise DatabaseNotFoundError(
                f"{DB_DIR}/ in {root} holds no {DB_FILE}; "
                f"check the path, or run `codebugs init {root}` to finish creating it"
            )
        return path, False, ()

    declared, source = declared_tracker_root()
    if declared is not None:
        return _declared_db_path(declared, source), False, ()

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

    root, unexamined = _walk_db_root(cwd)
    if root is None:
        # Both refusals below come in two spellings, and the empty-list one is
        # BYTE-IDENTICAL to what this function has always said. The other stops
        # asserting an absence it could not establish (CB-218): with a candidate
        # left unexamined, "no tracker in any parent" is a claim about something
        # nobody looked at — CB-203's defect, one function earlier.
        caveat = _unexamined_caveat(unexamined)
        # `is not None`, not truthiness: this module's own rule, and the reason
        # `exists` and `writable` are compared the same way two functions down.
        worktree = _enclosing_worktree_root(cwd)
        if worktree is not None:
            # Never advise `init` here: it would create a tracker that dies
            # with the worktree. Name the main checkout when we can find it.
            main = _worktree_main_root(Path(worktree) / DOT_GIT)
            where = f"in the main checkout ({main})" if main else "in the main checkout"
            found = (
                "and its main checkout has no tracker either"
                if not caveat
                else f"and none was found in its main checkout either{caveat}"
            )
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ found from {cwd}; this is a git worktree ({worktree}) "
                f"{found}. Run `codebugs init` "
                f"{where} — a tracker created inside a worktree is deleted with it",
                unexamined=unexamined,
            )
        if not caveat:
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ found in {cwd} or any parent; "
                f"run `codebugs init` here, or cd into a project that has one"
            )
        raise DatabaseNotFoundError(
            f"no {DB_DIR}/ found in {cwd} or any parent that could be examined"
            f"{caveat}; fix the path or its permissions, or cd into a project "
            f"that has one, or run `codebugs init` here",
            unexamined=unexamined,
        )
    # The one route allowed to create: see the asymmetry note above.
    return os.path.join(root, DB_DIR, DB_FILE), True, unexamined


def _db_path(project_dir: str | None = None) -> str:
    """The resolved DB path, without the may-create flag. See `_resolve_db`."""
    return _resolve_db(project_dir)[0]


# `os.access` authorizes with REAL credentials by default; EFFECTIVE ones are
# what actually decide a write, exactly the distinction `fsio.py`'s own
# `_ACCESS_KW` documents ("a false refusal is worse than the bug being
# fixed"). Not imported from there — that module's contract is to import
# nothing from this package — so the three-line idiom is reproduced rather
# than coupled to.
_ACCESS_KW: dict[str, bool] = (
    {"effective_ids": True} if os.access in os.supports_effective_ids else {}
)


def _writable_probe(path: str) -> bool | None:
    """Advisory tri-state: is an EXISTING `path` probably writable? (CB-100)

    Never call this for a path that has not already been confirmed to exist
    — `os.access` on a missing path returns False for an unrelated reason
    ("not there"), which would collide with the CB-23 "no database there
    yet" case `describe_root` already reports through `exists`, and would
    print two different sentences about the same absence.

    TWO checks, not one, and the second was earned by measurement rather
    than caution. `os.access` on a FILE consults only the file's own
    permission bits — it says nothing about the DIRECTORY holding it.
    Measured (a throwaway tracker, outside this repository): with
    `.codebugs/` at `chmod 555` and `findings.db` left at `0644`,
    `os.access(findings.db, W_OK, effective_ids=True)` reports True while
    every verb genuinely refuses with `attempt to write a readonly
    database` — sqlite needs to create a `-wal`/`-journal` sibling in the
    directory, which the directory's own bit forbids regardless of the
    file's. A file-only check would silently miss exactly the one of the
    three reproduced CB-100 states that decides the answer: it would check
    something, but not the thing sqlite actually consults. The other two
    reproduced states (`chmod 000`/`chmod 444` on the file itself) are
    caught by the file check alone — also measured, on the same tracker.

    THE REJECTED ALTERNATIVE, and why, because the choice was a measurement,
    not a preference (CB-100 §4). The obvious "authoritative" candidate is a
    real probe through `_open` — the same code every verb uses, so a
    positive answer would be a FACT rather than advice. Measured instead of
    assumed: on an up-to-date tracker `_open`, called with `create=False`,
    leaves the main database file byte-identical (same sha256, same inode) once the
    connection closes — so it does not persist a mutation on the ordinary
    path — but it is a REAL BEGIN-IMMEDIATE-shaped write attempt, and under
    ordinary, healthy contention (another verb mid-transaction) it BLOCKS
    for the full `busy_timeout=5000` and then raises `OperationalError:
    database is locked` — measured, 5.005s. A diagnostic that can hang a
    `codebugs where` call, or the MCP startup preflight, for five seconds
    under completely normal concurrent use is worse than the false-positive
    risk this function accepts, and it also creates transient `-wal`/`-shm`
    files for the live of the connection — "creates files" is a real cost
    even where the content ends up unchanged. `_open` is rejected on both
    counts; this advisory probe is the chosen mechanism.

    Returns True when NEITHER check found a reason to refuse — "not
    provably unwritable", never "will succeed": this is check-then-act like
    every `os.access` use, and the permission it reports can be revoked
    before the next statement runs. `describe_root`'s callers never print
    this value for exactly that reason — see `describe_root`. Returns False
    when either check found a reason to refuse: a NEGATIVE `os.access`
    answer cannot be revoked into a false one the way a positive one can
    (a right can be pulled between check and use; one can rarely be
    granted), which is why it is the value worth printing. Returns None
    when the probe itself could not be run — `os.access` raised, for a
    reason unrelated to permissions — and None must never collapse into
    either True or False, or "could not look" reads as either a clean bill
    of health or a false alarm, the "guard reporting clean because it could
    not look" shape this direction exists to close.

    WHAT IT COVERS AND WHAT IT DOES NOT — named here because the omission was
    itself a finding (CB-201, item 3). `_is_environmental`'s docstring lists the
    family this diagnostic sits in front of: read-only mount, read-only file,
    unwritable directory, full disk, I/O error on network storage. This probe
    answers about the first three, because `os.access(W_OK)` consults the
    permission bits and the mount's read-only flag and nothing else. It is BLIND
    to the last two, and blind to every non-permission reason a tracker will not
    work: a full disk (the space is consumed after the answer, not before), an
    I/O error, and — measured, and the reason CB-201 was filed — a `findings.db`
    whose CONTENT is not a database at all. In that last state this returns
    True, `codebugs where` prints a clean binding, and the first verb to open
    the file dies. That is not a false answer to this function's question; it is
    the question being narrower than "is this tracker usable", and no `os.access`
    call can widen it. Whether the resulting raw traceback is acceptable is
    `_is_environmental`'s business, where `SQLITE_NOTADB` is already recorded as
    unclassifiable.

    THE `except` ARM IS INSURANCE AND IS MEASURED DEAD; a mutant deleting it
    survives, and this says so rather than implying coverage (CB-201, item 4).
    `os.access` does not raise on a path that is missing, is behind a symlink
    loop, is reached through a non-directory, or is 5000 bytes long — it returns
    False, measured on every one. The single exception it does raise is
    `ValueError` for a NUL byte in the path, which the arm did NOT catch while
    it claimed to be the tri-state's "could not look" branch. It is caught now,
    so the declared contract and the code agree. Reachability from
    `describe_root` is nil BY CONSTRUCTION rather than by luck: `_path_state`
    runs first, refuses any path it could not `lstat`, and a NUL-bearing path
    raises there — where it IS caught and reported. The arm stays for the next
    caller and for the never-raises contract above it.
    """
    file_ok = _access_probe(path)
    dir_ok = _access_probe(os.path.dirname(path))
    if file_ok is None or dir_ok is None:
        return None
    return file_ok and dir_ok


def _access_probe(path: str) -> bool | None:
    """One `os.access(W_OK)` on an EXISTING path, tri-state. See `_writable_probe`.

    Split out of `_writable_probe` because CB-219 needs the DIRECTORY half on its
    own: when the database file is proven absent, what decides whether the next
    command can create one is the `.codebugs/` directory, and asking
    `_writable_probe` about that directory would additionally consult the
    PROJECT ROOT above it — a directory nothing is about to be created in. A
    false negative there would print a warning about a tracker that works, which
    is the CB-100 disagreement inverted and is exactly what this diagnostic must
    not do.

    Splitting rather than copying is the point: one definition of the tri-state,
    so the two questions cannot drift into two different treatments of *could not
    look*. The composition is unchanged and exactly equivalent to the single
    `try` it replaces — under the old spelling a raise from either call left the
    function at None, and here either None does.

    The `except` arm's honest status is `_writable_probe`'s: measured dead for
    every reason but a NUL byte in the path, kept for the contract above it.
    """
    try:
        return os.access(path, os.W_OK, **_ACCESS_KW)
    except (OSError, ValueError):
        return None


def describe_root() -> dict[str, Any]:
    """Report where this process is bound and which channel decided it.

    NEVER RAISES. It is the shared body of `codebugs where` and the MCP startup
    preflight, and the preflight must be warn-only: a fatal one would kill a
    server whose project only appears later, which is the self-healing property
    CB-11's card explicitly protects.

    One resolver, two consumers, so the diagnostic and the server can never
    disagree about where the process is pointed.

    `exists` IS THREE-VALUED, and the third value is the point (CB-203). `True`
    means a regular file was seen there; `False` means it was PROVEN that
    nothing is at that name — the CB-23 half-made tracker, the one state where
    the next command really does create the database; `None` means the question
    could not be answered, and `exists_reason` then carries a short sentence
    saying why. A caller may claim presence or absence only on the matching
    affirmative value, exactly as `reconcile.live_source_clause` hides a row
    only on affirmative proof. Truthiness reads `None` as `False`, so every
    consumer must compare with `is`.

    THE THIRD VALUE IS THE DEFAULT, NOT AN EXTRA CASE, and that is what makes
    this fix outlive the list of states anyone thought of. `_path_state` returns
    `absent` on exactly one measured condition (`ENOENT` on the name itself) and
    `None` on every other way of failing to see a file. So a mechanism nobody
    here has enumerated — a filesystem that answers with an errno this tree has
    never seen, some future mount type — lands in *could not tell* by
    construction rather than in *there is nothing there*. CB-203 reached
    production precisely because the enumeration was three permission states
    long and the population was not.

    `exists_reason` is UNCONDITIONAL, following the `attention` /
    `stripped_meta_keys` discipline: it is `None` when there is nothing to say,
    never absent, so a consumer can never read a missing key as "no such
    channel".

    `writable` (CB-100) is a SEPARATE key, deliberately never folded into
    `exists` — this module's own convention is that resolving is not the
    same as being there (CB-23), and a third meaning on `exists` would
    repeat that exact conflation. It is `None` whenever `exists` is not
    `True`: writability of a file that is not there is not a question this
    function answers, and computing it only when `exists` keeps the CB-23
    "no database there yet" line the one thing said about that state,
    rather than risking a second, colliding line. See `_writable_probe` for
    the tri-state and the mechanism measurement behind it. Callers print
    this value ONLY when it is `False` — never when `True`, because
    `os.access` is check-then-act and a diagnostic that says "writable" and
    is then refused is the same class of disagreement CB-100 exists to
    close, merely inverted; and never when `None`, because silence is what
    "could not determine" must look like here, for the same reason `exists`
    silence must never mean "healthy".

    `dir_writable` (CB-219) is `writable`'s mirror: `None` unless `exists` is
    `False`, and then the `.codebugs/` DIRECTORY's advisory tri-state. It exists
    because `exists is False` is not merely a report — it is the one branch that
    makes a PROMISE, "the next command creates one", and a promise needs the
    check its own subject requires. Measured on the unfixed tree: an empty
    `.codebugs/` at `chmod 555` produced that promise at exit 0, and the very
    next verb refused with "cannot open findings.db … for writing". The
    classification was RIGHT (nothing is at that name) and the INFERENCE was
    wrong. Printed on the negative answer only, exactly like `writable`.

    `unexamined` (CB-218) is the upward walk's list of places whose question
    could not be answered — `(path, reason)` pairs, in walk order. It is
    UNCONDITIONAL and empty means *the route ran and skipped nothing*, never *no
    such channel*; the named and declared routes return empty because they do
    not walk at all. Consumers print a line only when it is non-empty, since a
    healthy walk has nothing to report and a diagnostic that speaks on every
    invocation is one nobody reads. Without it, a wrong bind caused by an
    unexaminable ancestor is INVISIBLE — the binding lines look perfect, which
    is the measured harm in `_walk_db_root`'s docstring.
    """
    declared, source = declared_tracker_root()
    try:
        path, _may_create, unexamined = _resolve_db()
    except (DatabaseNotFoundError, OSError) as e:
        # OSError as well, so "never raises" is true rather than aspirational:
        # the filesystem can fail underneath any of this, and a preflight that
        # dies is a fatal preflight no matter which exception killed it.
        return {
            "root": declared,
            "source": source,
            "source_label": SOURCE_LABELS[source],
            "path": None,
            # There is no path, so "is a database file there" is not a question
            # that was asked, let alone answered. `False` here would be the same
            # two-valued lie this function's `exists` key exists to stop, sitting
            # in the same dict. Both consumers return on `error` before reading
            # it; the value is set for the third one that will not.
            "exists": None,
            "exists_reason": "the tracker root could not be resolved",
            "error": str(e),
            "writable": None,
            "dir_writable": None,
            # Read off the exception rather than defaulted to `()`, because on
            # THIS branch the walk is exactly what failed, and reporting an empty
            # list for a walk that skipped something would make the key lie in
            # the one state it exists for. `isinstance`, not `getattr` with a
            # default — an `OSError` genuinely carries no such answer, and the
            # difference between *no walk happened* and *the attribute is
            # missing* is the difference this key is built on.
            "unexamined": e.unexamined if isinstance(e, DatabaseNotFoundError) else (),
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
    kind, detail = _path_state(path)
    if kind == PATH_FILE:
        exists: bool | None = True
        exists_reason = None
    elif kind == PATH_ABSENT:
        exists = False
        exists_reason = None
    elif kind is None:
        exists = None
        exists_reason = detail
    else:
        exists = None
        exists_reason = (
            "a directory is there, not a database file"
            if kind == PATH_DIR
            else f"{detail} is there, not a database file"
        )
    return {
        "root": os.path.dirname(os.path.dirname(path)),
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "path": path,
        "exists": exists,
        "exists_reason": exists_reason,
        "error": None,
        # `is True`, not truthiness — the probe's own docstring forbids running
        # it on a path not CONFIRMED to exist, and `None` is now a value `exists`
        # can hold. Truthiness happens to agree today; spelling it out is what
        # stops the next edit from disagreeing.
        "writable": _writable_probe(path) if exists is True else None,
        # CB-219's key, and the mirror image of `writable`: asked ONLY when the
        # database file is PROVEN absent, which is the one state whose whole
        # meaning is a promise about the future ("the next command creates
        # one"). The subject is the `.codebugs/` DIRECTORY, which the walk has
        # just proven is there, so `_access_probe`'s never-call-me-on-a-missing-
        # path rule is satisfied by construction. `is False`, again — and the
        # consumers print only the negative answer, for `writable`'s reason.
        "dir_writable": _access_probe(os.path.dirname(path)) if exists is False else None,
        "unexamined": unexamined,
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
            grouping,
            loc,
            merge,
            milestones,
            provenance,
            relations,
            reqs,
            similarity,
            sweep,
            usage,
        )

        _modules_loaded = True


def _unwritable(path: str, exc: BaseException) -> TrackerUnwritableError:
    """The ONE construction of the CB-86 refusal, for both of `_open`'s arms.

    The two arms fire on DIFFERENT predicates — the `create=False` arm also
    requires `os.path.exists`, because `SQLITE_CANTOPEN` is ambiguous there — and
    that is exactly why the message must not be typed twice. `CLAUDE.md` records
    the general form from CB-57: *sharing an implementation does not share a
    decision if the callers supply different inputs*. Here the reverse is what
    matters — two decisions may still share one sentence, and the end-to-end gate
    on that sentence is a substring (`"for writing" in stderr`), which cannot see
    one copy drifting.
    """
    return TrackerUnwritableError(
        f"cannot open {DB_FILE} at {path} for writing ({exc}); "
        f"check permissions on the file and its directory, and free disk space"
    )


def _open(path: str, *, create: bool) -> sqlite3.Connection:
    """Open a connection at an EXACT path and apply every module's schema.

    Raises `DatabaseNotFoundError` when `create=False` and the database is not
    there, and `TrackerUnwritableError` (CB-86) when it IS there and the
    environment refuses the write — a read-only mount or file, an unwritable
    directory, a full disk. Contention propagates as `sqlite3.OperationalError`
    so `cli.main`'s exit-5 arm still sees it, and any other sqlite failure keeps
    its traceback.

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
            # CB-86. SQLITE_CANTOPEN (14) is returned for BOTH "the file is not
            # there" and "the file is there and I may not open it", with the same
            # message — measured, `exists=False` and `exists=True` both give
            # `SQLITE_CANTOPEN: unable to open database file`. So the code alone
            # cannot choose between these two answers, and classifying 14 as
            # environmental unconditionally would tell someone whose tracker is
            # genuinely MISSING that their permissions are wrong.
            #
            # `os.path.exists` decides, and ONLY for message selection — exactly
            # what this function's docstring already says the resolver's `isfile`
            # check is for. The refusal stays race-free because the open enforces
            # existence; this only picks which true thing to say afterwards.
            #
            # KNOWN LIMIT: with an unreadable PARENT directory `exists` returns
            # False, so that case still reports "not found" for what is really a
            # permission problem. No worse than before, and narrowing it would
            # mean stat-ing every ancestor.
            if _is_environmental(e) and os.path.exists(path):
                raise _unwritable(path, e) from e
            raise DatabaseNotFoundError(
                f"no readable {DB_FILE} at {path} ({e}); "
                f"run `codebugs init` for that project, or check the path"
            ) from e
    # CB-86. THE OTHER THREE RAISE SITES ARE ALL BELOW, AND ALL INSIDE THIS
    # FUNCTION — which is the property the whole design rests on, and it was
    # verified by running each shape rather than assumed:
    #   - `sqlite3.connect(path)` on the create route  -> SQLITE_CANTOPEN
    #   - the WAL pragma                               -> SQLITE_READONLY (ext. 1544)
    #   - a module's `ensure_schema`, e.g. merge.py:99 -> SQLITE_READONLY
    # The third is the one that could have broken the design: it raises from
    # another module, several frames down, yet still inside `_open`. Because it
    # is, the exception type can honestly say "this happened while opening a
    # connection".
    #
    # THAT THIRD SITE IS CONDITIONAL SINCE CB-195, AND THE CLAIM ABOVE IT WAS
    # NARROWED TO MATCH (CB-199, open by design; the same narrowing is recorded
    # in CLAUDE.md immediately after the CB-86 rule). This comment used to end
    # "one classification point covers every SHAPE", and that was true only
    # because `merge.ensure_schema` wrote UNCONDITIONALLY on every open — an
    # accident, never a designed probe. CB-195 gated those seed inserts behind a
    # read, so on an established tracker `_open` attempts no write of its own and
    # a read-only DATABASE FILE reached through the walk is no longer detected
    # here at all. The honest claim is therefore: one classification point covers
    # OPENING A CONNECTION. A write that fails on an ALREADY-OPEN connection to a
    # read-only file is outside this try, and surfaces at the domain's own INSERT
    # as a raw traceback — exit code unchanged, nothing landed, only the message
    # narrowed.
    #
    # DO NOT "RESTORE" AN UNCONDITIONAL WRITE HERE TO WIDEN IT BACK. Any probe
    # that could detect a read-only file early must attempt a write on EVERY
    # `db.connect()`, which is verbatim the write-lock-on-read defect CB-195
    # exists to remove; a `busy_timeout=0` variant only turns "someone else is
    # writing" into an instant refusal, which is the same defect, faster. The
    # unresolved design question — early diagnosis without a write per open —
    # is what CB-199 is kept open to carry, and it is not answered by this
    # comment.
    #
    # Contention is re-raised untouched so `cli.main`'s exit-5 arm still sees it,
    # and anything not on the environmental allowlist keeps its traceback — a
    # genuine SQLITE_ERROR from a bug in this package must stay loud.
    try:
        if create:
            conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # Explicit; was inherited from sqlite3.connect(timeout=5.0)'s default. This is
        # what turns a losing writer into a clean rowcount=0 instead of an exception.
        conn.execute("PRAGMA busy_timeout=5000")

        _ensure_modules_loaded()
        for entry in _resolved_order():
            entry.ensure_fn(conn)
    except sqlite3.OperationalError as e:
        if is_contention(e) or not _is_environmental(e):
            raise
        # NO EXISTENCE CHECK HERE, and the asymmetry with the arm above is
        # deliberate rather than an oversight — a reader must be able to tell
        # those apart, so it is written down. On `create=True` the file legitimately
        # does NOT exist yet, so `os.path.exists(path)` would be False on the
        # ordinary path and the check could not mean what it means above.
        #
        # KNOWN GAP, accepted: if `.codebugs/` itself vanishes between
        # `_resolve_db` and this open — the CB-23 race window — the user is told to
        # check permissions for a tracker that is gone. The honest discriminator
        # would be `os.path.isdir(os.path.dirname(path))`, and it is NOT added
        # because there is no reproducer for it: adding a third predicate to an
        # allowlist-shaped refusal on reasoning alone is how the enumerations this
        # repo keeps re-filing get their extra dead entries.
        raise _unwritable(path, e) from e

    return conn


def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open the codebugs database.

    Creates one only on the upward-walk route, and only inside a `.codebugs/`
    directory that already exists — the deliberate asymmetry documented on
    `_resolve_db` (CB-23). A named `project_dir` or a declared root is opened
    `mode=rw` and never creates anything.

    Raises `DatabaseNotFoundError`, `TrackerUnwritableError` (CB-86), or a
    contention `sqlite3.OperationalError`; see `_open` for which is which.
    """
    # The third element is the walk's unexamined candidates, which only the
    # diagnostic consumers report; connecting neither needs nor may act on it.
    path, may_create, _unexamined = _resolve_db(project_dir)
    return _open(path, create=may_create)
