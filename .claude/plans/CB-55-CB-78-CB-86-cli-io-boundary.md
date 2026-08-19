# Cluster: CB-78 + CB-86 + CB-55 — the CLI's I/O error boundary

Branch `fix/cb-55-cb-78-cb-86-cli-io-boundary`, base `39d176a` (main, 1536 tests green).

## Why one tree

**Predicate 1 — same root cause.** The CLI has one error boundary (`cli.py:141-155`) and no
design for what it may catch. The same user-visible failure — *a CLI verb prints a Python
traceback for an environment problem* — has now been closed in three vocabularies (CB-71
`open(`, CB-79 ambient `OSError`, and neither could reach `sqlite3.OperationalError` or the
interpreter's own stdout flush). CB-78 and CB-86 each want to ADD something to that boundary;
CB-55 CONSTRAINS what may go in it.

**Falsifiable evidence that they cannot be designed independently:** CB-78's stated fix is an
arm at `cli.main` for a closed stdout, and the exception it would catch is `BrokenPipeError`
— *an `OSError` subclass*. CB-55's ratified constraint, written on the card, is that `OSError`
**must not** be caught at that boundary, because a central arm cannot tell a pre-write failure
from a post-write one. Read separately, the two cards prescribe contradictory changes to the
same eleven lines. That contradiction has to be resolved once, and resolving it is what this
tree does. (Resolution: CB-78 is fixed by a *signal disposition*, not by an arm, so no `OSError`
reaches the boundary at all and CB-55's constraint survives untouched.)

Note also that `reqs.py:1080-1085` already carries a comment deferring precisely this question
to CB-78 — the deferral is in the source, not only in the tracker.

## Ratified decision (user, 2026-08-19)

`codebugs <verb> | head` **dies by SIGPIPE, exit 141.** Explicitly rejected: "silent, exit 0",
because `codebugs export-csv - | gzip > backup.gz` with a dying `gzip` would then report success
over a truncated backup — a success-shaped lie in the one place CB-97 just made byte-verbatim.
Also rejected: "silent, exit 1" (CPython's own recipe), which keeps the defect the card was
filed for. 141 is the only option that preserves the distinction between *the reader went away*
and *the command failed*.

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Set `SIGPIPE` to `SIG_DFL` at the top of `main()`, guarded by `hasattr` | `cli.py::main` | CB-78 (FULL) | New test spawns the real CLI into a closing reader in BOTH buffering modes; asserts rc 141 and empty stderr, and that the write landed. Verified to fail against `git show main:src/codebugs/cli.py`. |
| 2 | New `db.is_environmental(exc)` (allowlist of primary sqlite codes) + a third arm at the `cli.main` boundary that prints one line and exits 1 for those, and re-raises otherwise | `db.py` (new fn near `is_contention`), `cli.py:146-155` | CB-86 (FULL) | Unit tests over the classifier on measured codes + the must-not-classify code; an end-to-end test running a write verb against a `chmod 555` tracker directory asserting one stderr line, no traceback, exit 1. |
| 3 | Replace 6 copies of `except OSError as e: print(f"codebugs: {e}", file=sys.stderr); [conn.close()]; sys.exit(1)` with one `cli.exit_on_io_error()` context manager wrapping the identical region | `cli.py` (helper), `findings.py:2333/2379/2451`, `reqs.py:1067/1088`, `bench.py:987` | CB-55 (PARTIAL — no trailer) | Existing tests for each of the six sites must stay green unchanged (they pin the one-line message and exit 1); plus a ratchet test asserting no bare copy of the arm survives in a CLI handler. |

Three rows, ceiling is four. Row 4 (the `json.JSONDecodeError`-before-`ValueError` half of CB-55)
is **deliberately not in this tree** — see *Out of scope*.

---

## CB-78 — a committed write reports failure when stdout closes

### Reproducer (run 2026-08-19 against `39d176a`, in a scratch tracker outside any worktree)

```
$ codebugs add -f x.py --description "pipe lie unbuffered" --category test --severity low | true
    # python -u:      BrokenPipeError traceback at findings.py:2216, EXIT=1
    # block buffered: "Exception ignored on flushing sys.stdout: BrokenPipeError", EXIT=120
$ codebugs query --status open | grep -c "pipe lie"
2          # BOTH writes landed
```

### Root cause

`findings.py:2216` is `print(f"Added: {result['id']}")`, which runs **after** the domain call
committed. Python installs `SIG_IGN` for `SIGPIPE` at startup, so the kernel's "reader is gone"
signal is converted into an `errno EPIPE` write failure, surfacing as `BrokenPipeError`. Under
block buffering the failing write is not the `print` at all — it is the interpreter's final
flush at shutdown, which is **outside every handler**, so no arm placement anywhere in this
package can reach it. That is why the fix is a signal disposition and not an `except`.

### Evidence (measured 2026-08-19, `sigpipe_probe.py`, 5000 lines into `head -1`)

| `SIGPIPE` disposition | buffering | exit code | stderr bytes |
|---|---|---|---|
| Python default (`SIG_IGN`) | `-u` | 1 | 252 |
| Python default (`SIG_IGN`) | block | 120 | 337 |
| `SIG_DFL` | `-u` | **141** | **0** |
| `SIG_DFL` | block | **141** | **0** |

One line fixes both modes, including the one no handler can reach. 141 = 128 + 13 (`SIGPIPE`).

### Plan

In `cli.main()`, before argument parsing:

```python
# CB-78. Python installs SIG_IGN for SIGPIPE, which turns "the reader went away"
# into an EPIPE write failure — a traceback and exit 1 when unbuffered, and
# "Exception ignored on flushing sys.stdout" with exit 120 when block-buffered,
# the latter raised at interpreter shutdown where no `except` can reach it. Both
# reported a mutation that had ALREADY COMMITTED as a failure. SIG_DFL restores
# the POSIX behaviour every other Unix filter has: the process dies by signal
# (141), which is distinguishable from a real failure (1) — the property the
# rejected "silent exit 0" would have destroyed for `export-csv | gzip`.
# hasattr: SIGPIPE does not exist on Windows. signal.signal is main-thread only,
# which main() is.
```

**Deliberate side effect, in our favour:** child processes inherit the disposition, so the
`git` subprocesses in `provenance.py` and `db.git_rev_parse` stop inheriting Python's
`SIG_IGN` — the long-standing CPython footgun. Nothing in this package pipes into those
children, so no behaviour changes today; it removes a latent one.

### Verification

New `tests/test_cli_boundary.py::TestClosedStdout`, running the real CLI as a subprocess
(`sys.executable -m codebugs.cli`) against a `tmp_path` tracker, stdout on a pipe closed by the
reader, in both buffering modes. Asserts `returncode == -13` (Popen reports the signal as a
negative number) or 141 via a shell, **empty stderr**, and that the row is present afterwards.
Proven to fail against `main`'s `cli.py`.

---

## CB-86 — a read-only tracker prints a traceback

### Reproducer

Already CONFIRMED and recorded on the card by iteration 2 (2026-08-17, against `d748a03`), in
an isolated detached checkout. Two shapes, both raw tracebacks, both
`sqlite3.OperationalError("attempt to write a readonly database")`:

- **A. tracker DIRECTORY read-only** (`chmod 555 .codebugs`) — raised at `db.py:1096`,
  `conn.execute("PRAGMA journal_mode=WAL")` inside `_open`. **Pre-write**, during connection setup.
- **B. database FILE read-only** (`chmod 444 findings.db`) — raised later, from a write statement
  during schema setup.

Both escape because `cli.py:152-153` re-raises anything `db.is_contention` does not match.
I re-measure both in this tree before editing.

### Root cause

`sqlite3.OperationalError` derives from `sqlite3.Error → Exception`, **not** from `OSError`, so
CB-71's `open(`-shaped sweep and CB-79's `OSError` widening were both structurally blind to it.
`is_contention` matches `{5, 6}` on purpose — a contended write must stay retryable and
distinguishable — so widening *it* is the wrong repair (it would blur "retry me" with "your disk
is full", which `claims.py`'s `undetermined` contract depends on).

### Evidence — the classifier's allowlist, measured 2026-08-19

| shape | exception | `sqlite_errorcode` | `& 0xFF` | name |
|---|---|---|---|---|
| read-only directory | `OperationalError` | 1544 | **8** | `SQLITE_READONLY_DIRECTORY` |
| read-only file | `OperationalError` | 8 | **8** | `SQLITE_READONLY` |
| missing parent dir | `OperationalError` | 14 | **14** | `SQLITE_CANTOPEN` |
| `SELECT nosuchcol` | `OperationalError` | 1 | **1** | `SQLITE_ERROR` — must NOT classify |
| corrupt file | `DatabaseError` | 26 | 26 | `SQLITE_NOTADB` — **wrong exception class**, never reaches this arm |

Two things this measurement bought that reading could not: the extended code `1544` proves the
`& 0xFF` mask `is_contention` already uses is load-bearing here too, and `SQLITE_NOTADB` would
have been a **dead entry** in the allowlist had I added it from the card's prose.

### Plan

`db.is_environmental(exc)` beside `is_contention`, same shape (`getattr` + `& 0xFF` + frozenset),
allowlist `{3 PERM, 8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN}`. **Fail closed:** anything else
still re-raises with its traceback, so `SQLITE_ERROR` — the class a genuine bug in this package
raises — keeps its full diagnostic. Honest labelling in the code: 8 and 14 are measured here;
3, 10 and 13 are reasoned from SQLite's own documentation and are not synthetically reproduced.

New arm at `cli.py`, ordered **after** contention:

```python
except sqlite3.OperationalError as e:
    if db.is_contention(e):      -> exit 5   (unchanged)
    if db.is_environmental(e):   -> one line, exit 1
    raise                                    (unchanged)
```

**Why this cannot introduce a CB-15/CB-16 lie, stated because CB-55's constraint says a central
arm cannot tell pre- from post-write:** today these failures already exit **1** (via the
traceback). After the change they still exit **1**. The exit code — the only thing a caller
reads — is unchanged; only the presentation changes. No success is reported for a failure and
no failure for a success, in either direction. That argument does **not** extend to `OSError`,
where the alternative outcome is 0, which is exactly why row 3 keeps that arm per-handler.

### Verification

- `tests/test_cli_boundary.py::TestEnvironmentalSqlite` — table-driven over stub exceptions
  carrying each code (the `sqlite3` exception attributes are read-only, so the classifier is
  tested through a stub, the same technique `tests/test_claims.py:485` already uses), asserting
  `SQLITE_ERROR` is refused.
- An end-to-end subprocess test: `chmod 555` on the tracker directory, run a write verb, assert
  exit 1, a single `codebugs: ` line, and **no** `Traceback` in stderr. Proven to fail against
  `main`.

---

## CB-55 — the copied `OSError` arm (PARTIAL)

### Reproducer — this is a duplication card, so the reproducer is a count

Measured 2026-08-19 on `39d176a`: **six** live copies in CLI handlers of

```python
except OSError as e:
    print(f"codebugs: {e}", file=sys.stderr)
    [conn.close()]
    sys.exit(1)
```

at `findings.py:2333` (`_cmd_import_csv`), `findings.py:2379` (`_cmd_restore_csv`),
`findings.py:2451` (`_cmd_export_csv`), `reqs.py:1067` (`_cmd_reqs_import`),
`reqs.py:1088` (`_cmd_reqs_export`), `bench.py:987` (`_cmd_bench_import`). The card recorded
**five** as of CB-76 on 2026-08-17; CB-97's `restore-csv` added the sixth two days later. The
card's central claim — that this grows on its own — is now measured twice rather than predicted.

### Root cause

There is no shared host. `cli.py:36-38` (`init`) holds the house format as a single
non-duplicated instance, and every handler that later needed it re-typed it.

### Plan

`cli.exit_on_io_error(*, closing=None)` — a `contextmanager` whose `with` block IS the guarded
region, so the region stays **exactly** what each site guards today. This is the shape the card
specifies ("a PER-HANDLER helper that takes the guarded region explicitly … never a wider arm
further up the stack"), and it is `cli.py`-owned so `sys.exit` stays out of `fsio.py`, which
deliberately imports nothing from the package.

**Each of the six regions is preserved byte-for-byte** — the surrounding comments at every site
explain *why* the region is narrow (`bench.py:979-983` and `findings.py:2325-2329` both spell out
that a wider arm would report a landed import as bad input). Those comments stay.

**Import direction, and the reason it is safe:** `findings.py` / `reqs.py` / `bench.py` gain
`from codebugs import cli`. `cli.py` imports only `db` at module level and `db.py` imports no
domain module at module level, so there is no cycle in either import order — verified by
reading both files. Note the one cosmetic consequence: under `python -m codebugs.cli` the module
is loaded twice (once as `__main__`, once as `codebugs.cli`); the helper is a stateless function,
so this is harmless, and the installed console entry point does not do it at all.

### Verification

Every existing test covering these six sites must pass **unchanged** — they assert the exact
one-line message and exit 1, so they are the regression net for a pure refactor. Plus a ratchet
test in `tests/test_cli_boundary.py` asserting, by AST over `src/codebugs/`, that no CLI handler
contains a bare `except OSError` whose body is `print`+`sys.exit` — the same enforcement shape
`tests/test_fsio.py::TestWriteCallSitesRatchet` uses, and for the same reason: prose is the wrong
layer for a rule whose failure mode is "someone types it again".

### Why CB-55 gets NO trailer

The card's *title* is the `json.JSONDecodeError`-before-`ValueError` ordering arm, which this
tree does not touch (see below). Closing the card on the `OSError` half would misstate what
landed. Status stays `in_progress`; notes get the measured six-copy count and what remains.

---

## Out of scope, named rather than dropped

- **CB-55 row 4 — the `json.JSONDecodeError`-first re-raise arms** (`findings._cmd_update`,
  `_cmd_query`, `_cmd_add`; `reqs._cmd_reqs_query`, `_cmd_reqs_verify`; and the standing
  `_cmd_reqs_update` debt where it is MISSING). Excluded for three reasons, in order of weight:
  it is a **different transformation** with a different rule ("stored-data parse failure
  re-raises; input `ValueError`/`KeyError` prints and exits 1"), so by the diff-shape rule it is
  its own row; adding the arm to `_cmd_reqs_update` is a **behaviour change** that needs its own
  reproducer rather than riding along in a refactor; and taking it would put this tree at the
  hard ceiling of four rows with no headroom for review findings.
- **`claims._is_contention`** (`tests/test_claims.py:485` exercises it) is a second copy of
  `db.is_contention`. Noticed while reading; not touched, not filed yet — it is a sibling of this
  cluster's theme but neither card owns it and it needs its own look.
- **fd 1 closed (`EBADF`)**, listed as case (3) on CB-78. I could not reproduce it: `>&-` on a
  read verb exited **0** with no message on this build, so the card's `[Errno 9]` claim is
  unconfirmed. `SIG_DFL` does not cover `EBADF` (it is not a signal). If the reproduction is
  found, it is a follow-up card, not a silent widening of the boundary arm.

## Shared risks

1. **`SIG_DFL` changes an exit code every scripted consumer can see.** Mitigated by the
   measurement that the only in-repo consumer, `tools/worktree-setup.sh:104-105`, is immune
   twice over: `sed` drains the whole stream so `codebugs` never receives `SIGPIPE`, and the
   line ends in `|| true`. Re-verified before finish.
2. **A pure refactor across three modules (row 3) can silently narrow a guarded region.** The
   mitigation is that the six regions are preserved verbatim and the existing per-site tests are
   the net; the ratchet test then stops the arm being re-typed.
3. **A new import edge `domain → cli`.** Argued safe above; the adversary should attack this
   specifically, including under `--mode` isolation and under `python -m`.
