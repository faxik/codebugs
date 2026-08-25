"""Shared fail-closed guard for the tests/manual/ mutation harnesses (CB-173).

Both mutate_cb69.py and mutate_cb31.py write a mutated copy of a source file to
disk, run pytest, and restore the original in a `finally`. Nothing ever checked
the tree was clean before that first write — and that destroyed an agent's
uncommitted work five times (CB-173's cited incidents: four in one session,
2026-08-13, a fifth on 2026-08-17). `require_clean_tree` is the fix. Call it as
the FIRST thing a harness does, before `read_text()`: a harness that has
already started overwriting a file is not saved by a guard called afterwards.

Deliberately in scope only: this one guard, called by both existing scripts.
NOT a rewrite of either script's mutation mechanism and NOT a shared runner —
those are separate, out-of-scope forms (see CB-173's own card).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class DirtyTreeError(RuntimeError):
    """Raised when a mutation target has uncommitted changes, or when git
    could not be asked at all."""


def require_clean_tree(paths, *, cwd, allow_dirty=False):
    """Refuse to proceed if any of `paths` has uncommitted changes.

    `paths` are the files a harness is about to overwrite with a mutated
    version. `cwd` is the git worktree root the check runs in — `git status
    --porcelain` is scoped to exactly `paths`, so the answer is about the
    files the harness is actually going to touch, not the whole tree (an
    unrelated dirty file elsewhere must not block a probe that never writes
    to it).

    Fail-closed: an unreadable git — missing binary, a non-executable one,
    a non-zero exit — is treated as DIRTY, never as clean. "Could not look"
    must read as "assume the worst", the same class of guard this repository
    documents in CLAUDE.md under `_guard_interpreter_matches_main` — the
    opposite choice would be exactly the "guard reporting clean because it
    could not look" shape this repo has already caught four other times.
    Both `OSError` (git missing, or present but not executable) and
    `subprocess.SubprocessError` (e.g. a timeout) are caught — the second is
    NOT a subclass of the first (CB-79 in CLAUDE.md).

    `allow_dirty=True` is the explicit escape hatch a caller passes after
    deciding the risk is acceptable (a CLI flag or an environment variable
    at the call site) — without one, a refusal is a wall instead of a
    diagnostic.
    """
    if allow_dirty:
        return

    str_paths = [str(p) for p in paths]
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", *str_paths],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DirtyTreeError(
            f"could not ask git about {str_paths} ({exc!r}); refusing to "
            "mutate rather than assume the tree is clean. Commit or stash "
            "these files first, or pass allow_dirty=True to proceed anyway."
        ) from exc

    if proc.returncode != 0:
        raise DirtyTreeError(
            f"git status exited {proc.returncode} for {str_paths}: "
            f"{proc.stderr.strip()!r}; refusing to mutate rather than "
            "assume the tree is clean. Commit or stash these files first, "
            "or pass allow_dirty=True to proceed anyway."
        )

    dirty = proc.stdout.strip()
    if dirty:
        names = ", ".join(str(Path(p).name) for p in str_paths)
        raise DirtyTreeError(
            f"uncommitted changes in mutation target(s) [{names}]:\n{dirty}\n"
            "Commit or stash them first, or pass allow_dirty=True to "
            "proceed anyway — the probe would otherwise overwrite them and "
            "the restore in `finally` would restore the WRONG original."
        )
