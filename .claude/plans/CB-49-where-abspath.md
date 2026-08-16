# CB-49 — relative declared tracker root leaks into diagnostics

## Reproducer (verified 2026-08-16, from source)
cwd `scratchpad/i-a`, tracker in `scratchpad/i-b`:
- `codebugs --tracker-root ../i-b where` → `root: ../i-b` (relative)
- `CODEBUGS_ROOT=../i-b codebugs where` → same, second channel
- `codebugs --tracker-root ../nope where` → error text carries raw `../nope`
- `init` on the same flag prints the ABSOLUTE path (`init_project` abspaths) —
  two commands about one binding answer in different coordinate systems.

## Root cause
`db.declared_tracker_root()` (db.py:520) returns both channels' values verbatim.
Every consumer inherits the raw value: `_declared_db_path` error messages
(db.py:554-577), `describe_root` (both the error branch's `root: declared` and,
via `_db_path`, the resolved path), hence `codebugs where` and
`server._preflight`'s stderr line — the one reader who provably cannot know the
server's cwd.

## Plan
1. Normalize in `declared_tracker_root()` — the single read point for both
   channels — with `os.path.abspath` (lexical). NOT `realpath`: a declared root
   is often a deliberately symlinked path; the job is to make the declaration
   interpretable outside its cwd, not to rewrite it.
2. Guard: `abspath` on a relative value calls `os.getcwd()`, which raises when
   cwd was deleted — and `describe_root`'s NEVER-RAISES try begins after its
   `declared_tracker_root()` call. Fall back to the raw value on `OSError`;
   downstream `isdir` then fails closed exactly as today.
3. Read-time normalization deliberately matches when `_declared_db_path`'s
   `isfile` resolves the same relative value, so report == resolution.
4. Consumers checked compatible: `cli._cmd_init` passes `declared` to
   `init_project` (abspaths anyway) and compares via `realpath` both sides;
   its mismatch warning now prints absolute — an improvement.

## Verification
- New tests (fail-first proven): relative env + flag report absolute in
  `describe_root` and `where`; relative bad root names absolute in the error;
  relative declared root + deleted cwd does not raise.
- Existing `TestDescribeRoot` / `TestWhereCommand` pass (they use absolute
  declared paths, per the card's note on test_db_infra.py:975-985).
- Full suite in the worktree: `uv run --extra dev python -m pytest tests/ -q`.

## Risks / out of scope
- CB-45's branch also touches db.py/cli.py/server.py (similarity seam) —
  different hunks, merge risk accepted and flagged.
- No behavior change to resolution, only to reporting and error text.

## Review
Mechanical fix (one hunk + fallback helper + tests, no gate, no API change) —
cross-model adversarial review skipped per bugfix-loop's small-fix allowance.
