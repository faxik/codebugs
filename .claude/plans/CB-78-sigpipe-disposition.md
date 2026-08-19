# CB-78 — a closed stdout must not report a committed write as a failure

Branch `fix/cb-78-sigpipe-disposition`, base `39d176a` (main, 1536 tests green).
One card, one row, FULL.

## This started as a three-card cluster and was split by review, before any code

The first version of this plan bundled **CB-78 + CB-86 + CB-55** as "the CLI's I/O error
boundary". `/adversarial-review-x2` (Opus adversary + Codex/GPT-5.6-Sol in parallel, then an
Opus defender over the union) killed the cluster on its own stated rules, and the split is
recorded here rather than quietly performed:

- **The predicate never held.** The cluster claimed predicate 1 of
  `~/.claude/skills/_shared/bug-clustering.md` — *one causal code change makes every reproduced
  symptom disappear* — while its own edit table listed **three** causal changes, one per card.
  The table was the disproof of the predicate printed directly beneath it.
- **The "falsifiable evidence" destroyed itself.** It argued that CB-78's prescribed fix (an arm
  catching `BrokenPipeError`, an `OSError` subclass, at `cli.main`) contradicts CB-55's ratified
  constraint (no `OSError` at that boundary) — and then stated, two sentences later, that the
  shipped CB-78 fix is a *signal disposition* and adds no arm. A contradiction belonging to a
  design you rejected cannot justify a tree.
- **The edit count was over the hard ceiling of four** before counting anything else. CB-55's six
  call sites are **three distinct transformations** (measured by reading all six), so even the most
  generous "one row per shape" arithmetic gives 1 + 1 + 3 = 5.

CB-55 went back to `open` carrying the measured evidence (six copies, three shapes, zero CLI
coverage at `restore-csv`, and the reviewers' `cliio.py` recommendation instead of the `cli.py`
host its own note used to propose). CB-86's design was ratified in the same review — a typed
`db.TrackerUnwritableError` raised *inside* `db._open`, never a central `sqlite3.OperationalError`
arm — and is written on that card in full, awaiting `fix/cb-86-tracker-unwritable`.

## Ratified decision (user, twice)

**2026-08-19, first ratification:** `codebugs <verb> | head` dies by SIGPIPE, exit 141. Rejected:
"silent exit 0" (a truncated `export-csv … | gzip > backup.gz` would report success) and "silent
exit 1" (keeps the defect the card was filed for).

**2026-08-19, re-ratified after measurement contradicted the argument I used.** The rationale I
gave named `export-csv | gzip`, and that path turned out to be the ONE place the change makes
things worse: `fsio.atomic_write` writes `/dev/stdout` in place, so the CB-76 arm at
`findings.py:2451` already caught the `BrokenPipeError` and printed
`codebugs: [Errno 32] Broken pipe` at exit 1. There was no exit-0 path there to protect. 141 is
still right for every other verb, where the status quo is exit 120 + "Exception ignored" or exit 1
+ a raw traceback — but on the export path it **removes a working diagnostic**. The user was told
this and confirmed 141 as a single uniform rule. (My original example was also mis-spelled:
`export-csv -` writes a file literally named `-`; `output = args.file or "codebugs_export.csv"`.
The supported streaming spelling is `/dev/stdout`.)

## Reproducer — run 2026-08-19 against `39d176a`

```
$ codebugs add -f x.py --description "pipe lie unbuffered" --category test --severity low | true
    # python -u:      BrokenPipeError traceback at findings.py:2144, EXIT=1
    # block buffered: "Exception ignored on flushing sys.stdout: BrokenPipeError", EXIT=120
$ codebugs query --status open | grep -c "pipe lie"
2          # BOTH writes landed
```

## Root cause

`findings.py:2144` is `print(f"Added: {result['id']}")`, which runs **after** the domain call
committed. Python installs `SIG_IGN` for `SIGPIPE` at startup, so the kernel's "reader is gone"
signal becomes an `errno EPIPE` write failure surfacing as `BrokenPipeError`. Under block
buffering the failing write is not the `print` at all — it is the interpreter's final flush at
shutdown, which is **outside every handler**. That is why the fix is a signal disposition and not
an `except`: no arm placement anywhere in this package can reach the second case.

## Evidence

Measured with `sigpipe_probe.py` (5000 lines into `head -1`) **and** re-measured on the real verb,
because the two are different experiments and the first plan presented them as one:

| `SIGPIPE` disposition | buffering | exit code | stderr bytes |
|---|---|---|---|
| Python default (`SIG_IGN`) | `-u` | 1 | 252 |
| Python default (`SIG_IGN`) | block | 120 | 337 |
| `SIG_DFL` | `-u` | **141** | **0** |
| `SIG_DFL` | block | **141** | **0** |

Real verb (`codebugs add`, one line of output, row confirmed present afterwards): today 120 / 82
bytes block-buffered, 1 / traceback unbuffered; with `SIG_DFL`, 141 / 0 bytes in both. 141 = 128 + 13.

**When this is observable at all — two branches, and stating only the second is wrong.** Either the
reader closes *without draining* (any size: a 656-byte export into `( exit 0 )` reproduces), or
un-drained output exceeds the pipe buffer (`F_GETPIPE_SZ` = 65536 here; a single 65 537-byte write
into `head -1` still gives rc 0 because `head` drains it, 200 000 bytes gives EPIPE). A one-line
`add` or a 6.5 KB `query` piped to `head -1` never reaches the state.

## The shipped design, and the two properties that are load-bearing

`cli.run()` is the new **process** entry point: it installs `SIG_DFL` under
`hasattr(signal, "SIGPIPE")` and calls the unchanged `main()`. `pyproject.toml:28` points at it and
`if __name__ == "__main__": run()` covers `python -m codebugs.cli`.

1. **`run` is separate from `main` because `main` is imported and called IN-PROCESS** by
   `tests/test_fsio.py:134`, `tests/test_findings.py:2108` and
   `tests/manual/repro_cb76_truncation.py:69`, all deliberately (their docstrings say the
   shelled-out drafts "proved nothing"). `signal.signal` is an unrestored process-global mutation:
   installed inside `main` it leaves the whole pytest session under `SIG_DFL`, reproduced in review
   as `pytest -q -s . | head -2` dying at 141 mid-suite where the same file without the signal call
   exits 1. It also raises `ValueError: signal only works in main thread of the main interpreter`
   off the main thread, which `hasattr` does not cover.
2. **`run` must NEVER restore the previous disposition.** The obvious "polite" variant — install,
   call `main`, restore in a `finally` — was measured and **reintroduces the defect**: `add | true`
   under block buffering goes back to exit 120 and "Exception ignored on flushing sys.stdout",
   because that write is the shutdown flush and happens after `main` returns. "Do not mutate
   process-global state" and "fix the block-buffered case" are incompatible inside one function.
   The split is the only shape that makes both true.

**Deployment caveat, invisible in the diff:** a console shim generated before this change imports
`main` **by name** (verified by reading the installed `/home/faxik/.local/bin/codebugs`, a pipx
shim). An existing install keeps the old behaviour until `pipx reinstall codebugs` /
`pip install -e .` regenerates it. This is in the CHANGELOG as an explicit instruction, because
nothing in the tree can pin someone else's installed shim.

## Four artefacts the change makes FALSE, repaired in the same commit

None of these can land independently — each is false before the change and true after — so by
predicate 4 they are part of this row rather than a new one.

1. `reqs.py:1081-1084` said the stdout branch stays unwrapped because that is "CB-78's open
   question". This change answers it.
2. `bench.py:1000-1006` said "making it POSIX-clean instead is CB-78". Done. The comment now also
   records what did **not** change: `tests/test_bench.py:745` closes the `sys.stdout` OBJECT, which
   raises `ValueError: I/O operation on closed file` — not a signal — so that path still crashes
   with a traceback, which is the ratified discriminator at `tests/test_bench.py:789`.
3. `findings.py:2429-2433` — the `except OSError` at `:2451` becomes unreachable for pipe and FIFO
   destinations. Measured: `export-csv /dev/stdout | head -1` goes from exit 1 +
   `codebugs: [Errno 32] Broken pipe` to 141 + empty stderr. The comment now names this as the one
   place the card trades a working message for silence.
4. `tests/test_fsio.py:417` — `test_a_broken_pipe_on_the_stdout_branch_still_propagates` INJECTS the
   exception by monkeypatching `print`, so it stays green while pinning a state production can no
   longer reach. Renamed to `test_a_simulated_broken_pipe_…` and re-documented to say so, with a
   pointer to the real-pipe tests.

Plus `CLAUDE.md:511` (the shell-caller exit-code API gains 141) and a `CHANGELOG.md [Unreleased]`
entry carrying the cost, the observability condition, and the reinstall instruction.

## Verification

`tests/test_cli_signals.py`, 8 tests, subprocess-based on purpose — the block-buffered failure
happens after `main()` returns, so no in-process caller can see it.

**Mutation-proven: 7 of the 8 FAIL against `git show main:` versions of `cli.py` and
`pyproject.toml`**, and the mutation was confirmed to have landed (`grep -c "def run"` → 0, entry
point back to `main`) rather than assumed. The eighth,
`test_calling_main_leaves_the_sigpipe_disposition_alone`, passes on both sides **deliberately**: it
pins a property this change preserves, and its docstring says so, per CLAUDE.md's testing rule.

Coverage: both buffering modes on a write verb, with the landed row asserted as the premise; a read
verb (`query`) so an implementation that special-cased write verbs would fail; `export-csv
/dev/stdout` as the changed-behaviour pin; `main` leaving `signal.getsignal(SIGPIPE)` untouched; a
one-installation-site structural check; and the console-script/`__main__` declarations, which are
the "gate present in the tree, absent in effect" shape this repo keeps re-filing.

Full suite 1544 passed (1536 + 8), `ruff check` clean.

## Sibling sweep

Method: `[project.scripts]` plus `grep -rn '__name__ == "__main__"' src/`. **Exactly two process
entry points exist**, and the second is a deliberate non-instance: `codebugs-mcp` →
`server.main`, where stdout is the JSON-RPC **transport**, not a report stream. Dying silently at
141 when an MCP client disconnects is a different question with a different owner, and it is not
touched here. Also checked and found not to be an issue: `fsio.py:40` warns that an uncatchable
signal leaks the temp file, and `SIG_DFL` makes SIGPIPE such a signal — but no `atomic_write` block
writes to a pipe while holding a temp (the `/dev/stdout` case takes the write-in-place branch and
creates no temp), so no new leak is reachable.

## Out of scope, named rather than dropped

- **fd 1 closed (`EBADF`)**, case (3) on the card. Not reproduced: `python … >&-` exits **0** with
  empty stderr on this build, so the card's `[Errno 9]` claim is unconfirmed. `SIG_DFL` does not
  cover it (it is not a signal). A follow-up card if it is ever reproduced — not a silent widening.
- **`reqs.import_markdown`'s per-row `except sqlite3.Error: skipped += 1`** (`reqs.py:620-622`)
  counts an environmental SQLite failure as a malformed row and reports success. Found by Codex
  during this review; measured unreachable for CB-86's shapes; filed separately.
- **CB-55 and CB-86**, as above.
