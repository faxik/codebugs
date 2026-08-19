# CB-86 — a tracker the process cannot write must not crash, and must not be told to run `init`

Branch `fix/cb-86-tracker-unwritable`, base `978700f`. One card, one row, FULL with named limits.

## The design was ratified before this tree, and the boundary option was REFUSED with evidence

CB-86's open decision — *"a new classifier at the `cli.py` boundary, or an explicit arm at the
create path"* — was answered by `/adversarial-review-x2` + defender during the CB-78 iteration and
written onto the card in full. This plan implements that ruling; it does not re-open it.

**Why not the boundary.** The first design added `db.is_environmental(exc)` plus a third arm at
`cli.py:146-155`, justified by *"these failures already exit 1 via the traceback, so after the
change they still exit 1, therefore no new lie is possible"*. That argument is refuted by this
repository's own ratified test: `tests/test_bench.py:710-718` states that exit-code equality proves
**nothing** (*"Exit code 1 and 'the path appears on stderr' are BOTH satisfied by the unfixed
tree … neither discriminates"*), and `tests/test_bench.py:789` asserts `"Traceback" in r.stderr` as
the contract that distinguishes a post-commit failure from an input error. A central arm deletes
exactly that discriminator, at the one place that structurally cannot tell pre-write from
post-write. It also proves too much: a post-write `OSError` also exits 1 with a traceback today, so
the same reasoning would license the central `except OSError` that CB-55 forbids.

**What is built instead.** Classify inside `db._open` and raise a **typed** exception,
`db.TrackerUnwritableError`, a sibling of the existing `DatabaseNotFoundError`. The boundary then
catches a TYPE that carries its own provenance — *this happened while opening a connection* —
instead of inferring it. `cli.py:144` gains the type; **no** central `sqlite3.OperationalError` arm
is added.

## Reproducer — all four shapes, run 2026-08-19 against `978700f`

In a plain temp dir outside any worktree (a probe worktree inherits this repo's own tracker — the
card records that nearly writing to it).

| shape | condition | today |
|---|---|---|
| **A** | `chmod 555 .codebugs`, walk route, write verb | traceback, `db.py:1111` (WAL pragma), `attempt to write a readonly database`, exit 1 |
| **B** | `chmod 444 findings.db`, walk route, write verb | traceback, **`merge.py:80`**, reached from `db.py:1118` — inside `_open`'s `_resolved_order()` loop, exit 1 |
| **C** | `chmod 000 findings.db`, **named** route (`--tracker-root`) | `codebugs: no readable findings.db at … (unable to open database file); run `codebugs init` for that project` — exit 1, no traceback, and **wrong advice** |
| **C2** | `chmod 000 findings.db`, **walk** route | traceback, **`db.py:1109`** (`sqlite3.connect(path)`, the create branch), `unable to open database file`, exit 1 |

**Two corrections to the card, from running it rather than trusting it.** The card's line numbers
(`db.py:1086`, `:1089`, `:1096`) are all stale. And there are **three** raise sites inside `_open`,
not two: `:1109` (create-mode connect), `:1111` (the WAL pragma) and `:1118` (the `ensure_fn` loop).
Shape B raising at `merge.py:80` *through* `:1118` is the load-bearing confirmation — it is still
inside `_open`, so one classification point covers every shape.

## Root cause

`sqlite3.OperationalError` derives from `sqlite3.Error → Exception`, **not** `OSError`, so CB-71's
`open(`-shaped sweep and CB-79's `OSError` widening were both structurally blind to it — the third
vocabulary of "the CLI crashed at an I/O boundary". `is_contention` matches `{5, 6}` on purpose (a
contended write must stay retryable and distinguishable), so widening *it* is the wrong repair: it
would blur "retry me" with "your disk is full", which `claims.py`'s `undetermined` contract depends
on.

## The one design question the review did NOT cover, found while implementing

**`SQLITE_CANTOPEN` (14) is returned for BOTH "the file is not there" and "the file is there and I
may not open it", with an identical message.** Measured:

```
MISSING  : SQLITE_CANTOPEN (14) exists=False: unable to open database file
CHMOD 000: SQLITE_CANTOPEN (14) exists=True:  unable to open database file
```

So on the `create=False` route the error code alone cannot choose between `DatabaseNotFoundError`
("run `codebugs init`") and `TrackerUnwritableError` ("fix the permissions"). Classifying 14 as
environmental unconditionally would regress the main CB-23 path — a genuinely missing tracker would
start telling people their permissions are wrong.

**Resolution: `os.path.exists(path)` decides, and only for MESSAGE SELECTION.** That is precisely
what `_open`'s existing comment already says the resolver's `isfile` check is for (*"only supplies
a good message"*); the refusal itself stays race-free because it is the open that enforces
existence. **Residual limit, stated rather than discovered later:** with an unreadable *parent
directory* `os.path.exists` returns `False`, so that case is reported as "not found" when it is
really a permission problem. It is strictly no worse than today (which says the same thing), and
narrowing it would require a stat of every ancestor.

## Independent edits — one row

| # | Change shape | Locations | Card |
|---|---|---|---|
| 1 | New `TrackerUnwritableError` + `is_environmental()`; classify at all three raise sites inside `_open`; add the type to the CLI boundary arm | `db.py` (`_open`, near `is_contention`), `cli.py:144` | CB-86 FULL |

## The allowlist

`{8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN}`, masked `& 0xFF`.

- **8** and **14** are measured here (shapes A/B give extended `1544 & 0xFF == 8` and plain 8; C/C2
  give 14). **10** and **13** are reasoned from SQLite's documentation and are **not** synthetically
  reproduced — labelled as such in the code.
- **3 (`SQLITE_PERM`) is deliberately absent.** The review measured that `chmod 000` yields 14, not
  3, and found no CLI-reachable path producing it. Adding it would be a dead entry — the exact
  mistake the CB-78 plan congratulated itself for avoiding and then made three times.
- **26 (`SQLITE_NOTADB`) must NOT be added**: it arrives as `sqlite3.DatabaseError`, not
  `OperationalError`, so it can never reach this arm. Measured.
- **An unlisted code falls through to today's behaviour** — a traceback. So this enumeration fails
  toward the status quo, not toward a wrong answer. That is the honest reading of a rule this repo
  has now been bitten by six times.

## What this does NOT fix, and why FULL is still honest

1. **A post-connect environmental failure** (the disk fills mid-run, an NFS `SQLITE_IOERR`) still
   exits 1 with a traceback. Unchanged and **correct** — `tests/test_bench.py:789` ratifies the
   traceback as the post-commit discriminator, which is the whole reason the boundary arm was
   refused.
2. **`reqs.import_markdown`'s per-row `except sqlite3.Error: skipped += 1`** (`reqs.py:620-622`)
   would count an environmental failure as a malformed row. Measured unreachable for every shape
   here (all four die during `connect`). Filed as **CB-99**.
3. **The MCP server path** is untouched; a read-only tracker there still surfaces through the SDK.

## The guarantee, stated precisely rather than over-claimed

`TrackerUnwritableError` means *this failure happened while opening a connection*. That is
structural: `_open` raises before returning. It does **not** by itself prove no write landed earlier
in the process from a different connection.

The defender proposed pinning that stronger property with a ratchet over `db.connect()` call sites.
**Measured and rejected:** an AST count of `connect()` per function reports eight hits, and every one
is a `register_cli` **registrar** whose nested handler closures each connect once — so the naive
predicate is noise, and a correct one would have to model closure scope. No *handler* connects twice
today. The claim in the error message is therefore scoped to what `_open` can actually promise.

## Verification

- Classifier unit tests built by **assigning `sqlite_errorcode` onto REAL
  `sqlite3.OperationalError` instances** — measured assignable on 3.13.3 and exactly what
  `tests/test_claims.py:482` already does. Never a foreign stub class: one that is not an
  `OperationalError` instance could not exercise the arm and would be vacuous while looking like
  coverage.
- End-to-end coverage for **all four shapes**; A/B and C/C2 traverse different code paths
  (`db.py:1111` / `merge.py:80` vs `db.py:1109` / the `mode=rw` URI), so one is not evidence for the
  other.
- A test that a **missing** tracker still says `run codebugs init` — the regression the
  `os.path.exists` split exists to prevent.
- A test that a genuine programming-error code still raises rather than being laundered.
- POSIX skip guards, and `try/finally` permission restore so a failing test cannot leave a hostile
  directory behind.
- Every new test proven to fail against `git show main:src/codebugs/db.py`, with the mutation
  confirmed to have landed.
