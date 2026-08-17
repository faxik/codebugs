# CB-76 — an export that fails must not destroy the export it was replacing

**Card:** CB-76 (`low`, `error_handling`, `src/codebugs/findings.py`), filed 2026-08-17 out of
CB-71's sibling sweep; the truncation half is an Opus adversary finding (SERIOUS-3).
**Branch:** `fix/cb-76-atomic-export` · **Base:** `cd7d68a`
**Revision 2**, after `adversarial-review-x2` returned FAIL-REVISE on revision 1. The review
appendix is at the bottom; the headline is that **revision 1's fallback reproduced CB-76's own
defect**, and dropping it makes this design smaller, not larger.

## Why this card, and why alone

Focus was `codebugs`, "pure bugfixes I can confidently fix, start from the simple ones, batch
related ones together". CB-76 is fully reproduced, and its correct fix is named in the card body.
A bounded Codex pass over the shortlist (CB-76 / CB-79 / CB-73 / CB-68) ranked it first under this
focus and agreed with the batching refusal below.

**Batching refused, with the predicate named.** `bug-clustering.md` admits a second card only on
*same root cause* / *same edit seam **and** change shape* / *mandated-sweep hit* / *atomic landing*.
The nearest neighbour is CB-79 (`OSError` from non-file-open sources: an ambient `os.getcwd()`, and
a too-narrow `except` tuple in `provenance.resolve_trailers`). Its transformation is *guard an
ambient call* / *widen an existing tuple*; this card's is *hoist the write into an atomic
temp-then-replace and guard the region*. Different seam, different transformation — **"both raise
`OSError`" is taxonomy, not a predicate.** Two trees. Both reviewers confirmed this refusal.

The two *sites inside this card* do cluster, on predicate 2: one transformation rule, two locations,
identical before→after.

## Reproducer — both halves, on current main (`cd7d68a`), by running them

### (a) unwritable path → raw traceback, both handlers

```
$ codebugs export-csv /nonexistent-dir/x.csv
  File "src/codebugs/findings.py", line 1934, in _cmd_export_csv
    with open(output, "w", newline="") as f:
FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent-dir/x.csv'

$ codebugs reqs-export /nonexistent-dir/x.md
  File "src/codebugs/reqs.py", line 1047, in _cmd_reqs_export
    with open(args.file, "w") as f:
FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent-dir/x.md'
```

Neither handler has any `try`. (The card cites `findings.py:1919` / `reqs.py:1032`; main has moved
under it since `bc3f67e` — the real lines today are **1934** and **1047**, re-verified by reading.)

### (b) the half that matters: a failed export destroys the previous one

Committed as `tests/manual/repro_cb76_truncation.py` — revision 1 left it in a scratchpad, so a
reviewer could not re-run the plan's central evidence. It simulates `ENOSPC` at the csv writer
(what a full disk or a quota raises at that point; the query has already completed, so nothing
else is perturbed):

```
outcome              : OSError: [Errno 28] No space left on device  <-- escapes as a raw traceback
previous file bytes  : 34
file bytes afterwards: 0
previous export LOST : True
```

An earlier attempt injected the failure with `RLIMIT_FSIZE` and is recorded as a **negative
result**: the limit hits sqlite's WAL first and the run dies with `sqlite3.OperationalError: disk
I/O error` before reaching the export at all. Inject at the write, not at the process.

## Root cause

`open(path, "w")` truncates the destination **before** the first byte is written. The payload is
already fully in hand at that point — `query_findings` → `conn.close()` → `open` at
`findings.py:1930-1934`, and `export_markdown` → `conn.close()` → `open` at `reqs.py:1043-1047`
(verified by reading; pinned by a premise test, below). So truncation buys nothing and risks
everything.

**Why the obvious fix is a trap, and this is the whole point of the card.** Adding `except OSError:
print(...); sys.exit(1)` turns the traceback in (b) into one tidy line **over a file that is now
empty** — quieter, not safer. Worse, `import_markdown` (`reqs.py:564-566`) silently `continue`s on
any non-matching line and reports "Imported N, skipped M", so a truncated export round-trips as a
successful, empty import.

## Plan

One helper, two call sites, **one mode**.

### 1. `codebugs/fsio.py` (new) — `atomic_write(path, *, newline=None)`

**Not `fmt.py`.** Both reviewers rejected that placement independently: `fmt.py` is pinned by
`CLAUDE.md` as *"Shared CLI output utilities (ASCII table formatting)"*, it is the package's only
import-free module, and filesystem mutation is not formatting — "widening the docstring after
choosing the module is rationalization, not architectural justification" (Codex). A new 50-line
module with no domain imports is the honest home, and **the `CLAUDE.md` Architecture bullet is
edited in the same commit** so the entry cannot outlive its code (the CB-4/CB-5 failure).
Imported lazily inside `register_cli`, matching how both modules already import `format_table`
(`findings.py:1561`, `reqs.py:868`) — a module-level import would change domain/MCP import
behaviour.

The contract, stated as one sentence: **the destination is replaced only by a file that was
written and closed successfully, and the helper never grants access the plain `open` refused.**

Sequence:

1. `resolved = os.path.realpath(path)` — **first**, so `os.path.dirname` can never be `""` for the
   bare default `codebugs_export.csv` (`findings.py:1933`). The ordering is load-bearing; it is
   commented as such.
2. `st = os.lstat`/`os.stat(resolved)` if it exists. Dispatch on **what the destination is**, decided
   up front, never on a failure errno:
   - **missing, or a regular file** → the atomic path (3-7).
   - **not a regular file** (FIFO, char/block device, socket) → **write in place**, because there is
     no previous content to protect and `os.replace` would destroy the node. This is what makes
     `codebugs export-csv /dev/stdout` keep working; revision 1 would have swapped the redirect
     target's inode out from under the shell (measured by the Opus adversary).
   - **a directory** → raise the `IsADirectoryError` immediately with the *typed* path, before any
     temp is written.
3. If the destination exists, require `os.access(resolved, os.W_OK)` and raise `PermissionError`
   otherwise. **This is not belt-and-braces — it is a regression fix Codex found:** `open(w)` needs
   write permission on the *file*, `os.replace` needs it on the *directory*, so without this check a
   read-only destination inside a writable directory would be overwritten by a command that refuses
   today. The rule is now symmetric and statable: *the atomic path requires everything the old code
   required **and** everything replace requires.*
4. `tempfile.mkstemp(dir=os.path.dirname(resolved), prefix=".codebugs-export-")` — beside the
   resolved target, so same filesystem, so no `EXDEV`. The prefix makes a leaked temp attributable
   and hidden.
5. Yield `os.fdopen(fd, "w", newline=newline)`. **No `encoding=`**, deliberately and with a comment:
   `open()` and `os.fdopen()` both take the locale default today, and adding an explicit encoding
   would silently desynchronise export from import on a non-UTF-8 locale (Codex).
6. **Close before replace.** ENOSPC and quota failures commonly surface at flush/close, so the
   handle is closed — and its error allowed to propagate — *before* `os.replace` is reached. A
   replace over a handle that later fails to close would install a bad file while reporting failure.
7. `chmod` the temp: to the destination's existing mode if it existed, else `0o666 & ~umask`
   (`mkstemp` creates `0600`). The umask is read by `os.umask(0)` + restore, which is process-global
   — documented as safe *because* these are single-threaded CLI handlers, the same explicitness
   `CLAUDE.md` demands of the claims module's ambient-transaction invariant.
8. `os.replace(tmp, resolved)`.

Failure handling:

- Any exception → unlink the temp inside its own `try/except OSError: pass`, so **cleanup can never
  mask the real error**. That shape is already this repo's rule for `db.txn`'s guarded `ROLLBACK`
  (`db.py:507-512`); both reviewers required it here.
- `os.replace` failing *after* a good write is covered by the same cleanup, and is tested
  separately — revision 1's contract only covered exceptions in the body.
- Every `OSError` leaving the helper is re-raised as `OSError(errno, strerror, <typed path>)`, so
  the message names the path the user typed rather than a `.codebugs-export-XXXX` temp or a
  `realpath`-canonicalised absolute (CB-49's rule: a path in a diagnostic is only a report if its
  coordinate system travels with it).

**Stated behaviour change, deliberate, one case.** A destination that is *writable* inside a
directory that is *not* writable exports today and will now fail cleanly (`mkstemp` cannot create
there). Revision 1 fell back to a direct write here; the review reproduced data loss on that path,
and an errno-keyed fallback cannot distinguish "directory permissions" from ENOSPC/EDQUOT/inode
exhaustion — the very conditions the card is about. The two reviewers split here (Opus: keep it,
narrowed to `EACCES`/`EPERM`, and warn; Codex: no fallback, it cannot safely be inferred from
errno). I take Codex's side, because a helper named `atomic_write` that is sometimes not atomic is
a false contract at its own boundary, and because the user's standing rule is to **fail closed on
the unknown**. The case needs a directory with `r-x` holding a `rw` file; no such invocation exists
in this repo or its tests.

### 2. `findings.py::_cmd_export_csv` and `reqs.py::_cmd_reqs_export`

Identical transformation at both:

```python
# before
with open(output, "w", newline="") as f:
    ...write...
print(f"Exported ... to {output}")

# after
try:
    with atomic_write(output, newline="") as f:
        ...write...            # byte-for-byte unchanged
except OSError as e:
    print(f"codebugs: {e}", file=sys.stderr)
    sys.exit(1)
print(f"Exported ... to {output}")
```

`newline=""` for the CSV site, `newline=None` for the requirements site — the current parity, kept.
The message format is the house one already used by `init` (`cli.py:36-37`) and by CB-71's three
handlers. The success `print` **stays outside** the guard — verified by reading, it already sits
after the `with` at `findings.py:1973` / `reqs.py:1049` — so a post-write failure can never be
laundered into "bad input" (CB-15 / CB-16, and the live instance CB-71 measured).

**`reqs-export`'s stdout branch stays completely outside the helper and the guard.** Its path
argument is **positional and optional** (`reqs.py`, `p.add_argument("file", nargs="?")` — revision 1's
prose wrongly called it `--file`); with no path it `print`s to stdout, and pulling that into an
`except OSError` arm would change `BrokenPipeError` behaviour on a closed pipe, which is CB-78's
open question, not this card's.

## Verification

Partitioned, because revision 1 claimed all nine checks "fail against the pre-fix code" and **six
of them pass today** — the vacuous-test pattern this repo's ledger tracks. Group B is not padding;
it pins behaviour the change deliberately preserves, and each says so in its docstring so a reader
cannot mistake it for a defect proof.

**Group A — defect proofs. Each MUST fail against `cd7d68a` and I will run it there to show it.**

| # | Check | Discriminator |
|---|---|---|
| A1 | `export-csv` / `reqs-export` to an unwritable path | **`"Traceback" not in stderr`** — and *only* that. `returncode == 1` does not discriminate: an uncaught traceback also exits 1. Both CB-71 sibling classes say exactly this in their docstrings (`tests/test_findings.py:1616-1619`) and revision 1 dropped the discipline three days later. |
| A2 | A body-write failure leaves the previous export **byte-identical** | Injected **above both seams** at `csv.writer` / the markdown payload, so it fires on the unfixed tree (`open`) and the fixed tree (`os.fdopen`) alike — `CLAUDE.md`'s "to probe a commit seam, hook BOTH seams". Revision 1 named no injection point, and the obvious one (`builtins.open`) never fires on the fixed path, producing a *false failure against correct code*. |
| A3 | A **close/flush**-time failure leaves the previous export byte-identical | ENOSPC usually surfaces here, not at `writerow` (Codex). Distinct from A2. |
| A4 | An `os.replace` failure after a good write leaves the previous export intact, reports the **typed** path, and emits no success line | Revision 1's contract did not cover a failure outside the body at all. |
| A5 | No `.codebugs-export-*` temp survives any of A2-A4 | Asserted **only alongside** proof that a temp existed during the failure — otherwise it is green on a tree where the helper was never wired. |
| A6 | A read-only destination inside a writable directory is still refused | The permission-bypass regression. Fails against a naive `os.replace` implementation, not against `cd7d68a` — so it is a *fix-guard*, labelled as such. |

**Group B — compatibility pins. These pass on both sides, by design.** New-destination mode is
umask-derived not `0600`; an existing destination keeps its mode; a symlink destination stays a
symlink and its target receives the content; a FIFO/device destination is written in place, not
replaced; a directory destination errors with the typed path; `reqs-export` with no path still
writes to stdout **and a `BrokenPipeError` there still propagates rather than becoming
`SystemExit(1)`** (Codex: the naive version of this check passes whether or not the branch was
wrongly wrapped); exported bytes are identical to those the old handle produced, for both
`newline=""` and `newline=None`.

**Premise pin.** `TestExportPayloadIsInHandBeforeTheOpen` — the guard's safety rests on the payload
being complete and the connection closed before the write begins; that is prose today, and this
repo already has the pattern for pinning it (`tests/test_reqs.py:958`,
`TestImportMarkdownReadIsEagerPremise`).

**Not re-written:** the export→import round trip already exists and is green
(`tests/test_dedup.py:708`); revision 1 listed it as new work.

Then in the worktree: `uv run --extra dev python -m pytest tests/ -q` and
`uv run --extra dev ruff check src/ tests/`.

## Risks / out of scope

- **Ownership, ACLs, xattrs and hard-link aliases are not preserved** by replacement, and other
  hard links to the old inode keep the old content. Named here rather than in a helper docstring,
  because Codex is right that an internal docstring is not a user-visible compatibility contract.
- **Crash durability** — atomic *visibility* is not durability without `fsync` on file and
  directory. Out of scope; the card is about a failed write, not a power cut.
- **SIGKILL leaks a temp** that no cleanup can run. Bounded by the `.codebugs-export-` prefix, and
  stated rather than pretended away.
- **Concurrent exporters** to one destination go from interleaved garbage (an improvement) to
  last-writer-wins, with no locking. Declared, not fixed.
- **TOCTOU** between `realpath`/`stat`/`access` and `replace` — accepted for a single-shot CLI.
- Not fixing `import_markdown`'s silent acceptance of a truncated file (CB-51 / CB-77), the stdout
  branch (CB-78), the input-side handlers (CB-71, landed), or the non-file-open `OSError` sources
  (CB-79).

## Adversarial review x2 — what changed and who caught it

Revision 1 → 2. **Corroborated by both models** (highest confidence): the fallback is fatal and is a
product decision in disguise; verification items 3-9 cannot fail; non-regular destinations get
destructively replaced; `fmt.py` is the wrong home; `atomic_write` with a fallback is a false name;
cleanup must not mask the primary error; errors must report the typed path; the umask read is
process-global; SIGKILL leaks a temp.

**Codex-only:** the `os.replace` write-permission bypass (plan step 3 exists solely for it);
close/flush ordering vs replace (A3); byte-identity of output across the handle change; "can create
a temp" ≠ "can replace the destination".

**Opus-only:** data loss *reproduced on revision 1's own fallback trigger* (0o500 dir, 66→16 bytes);
the `/dev/stdout` realpath measurement; the `mkstemp(dir="")` ordering trap; the coordinate-system
mismatch between the success and failure messages; and that `reqs-export`'s argument is positional,
not `--file` — revision 1 was factually wrong about the CLI it was fixing.

**Where they disagreed:** the fallback. Opus would keep it narrowed to `EACCES`/`EPERM` with a
stderr warning; Codex would remove it. Removed — reasoning in the "stated behaviour change" note
above.
