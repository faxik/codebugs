# CB-71 — a CLI handler that performs file I/O must not report it as a traceback

Branch `fix/cb-71-cli-file-io-error-contract`, base `bc3f67e`. Focus: `codebugs`, pure bugfixes,
simplest first, batch related.

**Revision 2.** Revision 1 was reviewed by an Opus adversary and Codex/Sol in parallel and did not
survive: 5 FATAL + 5 SERIOUS from Opus, 4 FATAL-equivalents from Codex, with five findings
corroborated across both models. Every finding below was re-verified by running it before being
accepted or corrected. The corrections appendix records what changed and what I refused.

## The rule this tree applies

> A CLI handler that performs file I/O guards **exactly the I/O**, converts `OSError` into one line
> on stderr plus `sys.exit(1)`, and leaves **every success report and every prior mutation outside
> the guard** — because a failure raised after a write must surface as a crash, never as an
> input error.

House format for that line already exists and is matched, not invented: `cli.py:36` is
`except (ValueError, OSError) as e: print(f"codebugs: {e}", file=sys.stderr); sys.exit(1)`.
Revision 1 claimed "no existing arm catches `OSError`" — false, and the existing arm is the pattern.

## Scope: three edits, and why the other five reproduced defects are cards, not rows

Revision 1 proposed 5 independently landable edits regrouped as 3 rows "by file". Both reviewers
called that a miscount against `bug-clustering.md`'s hard stop of 4 and its rule that a row states
**one** before→after transformation. Correct: grouping by file is exactly what that rule forbids.

This tree is the **input side** — the three handlers that read a file — because they share one
transformation: *the read is guarded, nothing else moves*. The output side and the streaming
semantics are different transformations and are filed.

| # | Change shape | Location | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Hoist the success `print` out of the existing `try`; wrap `open`+`read` in `except OSError` → stderr + exit 1 | `bench.py` `_cmd_bench_import` | CB-71 | `uv run --extra dev python -m pytest tests/test_bench.py -q` + reproducers 1, 2, 7 |
| 2 | Add the missing arm (none exists) + `finally: conn.close()`; guard the `import_markdown` call | `reqs.py` `_cmd_reqs_import` | — (sweep) | `uv run --extra dev python -m pytest tests/test_reqs.py -q` + reproducer 3 |
| 3 | Hoist `f = open(...)` out of the `with`, guard **only** that; add `finally: conn.close()`; loop untouched | `findings.py` `_cmd_import_csv` | — (sweep) | `uv run --extra dev python -m pytest tests/test_findings.py -q` + reproducer 5 |

Unfiled sibling sites need no clustering predicate (`bugfix-loop` SKILL.md:65-67 exempts sweep hits
from the card count, and predicate 3 explicitly must not be invented in this workflow) — but the
**ceiling still governs**, which is where revision 1 broke.

Filed instead of fixed here, each with the reproducer that found it:

- **The two export handlers** (`reqs.py:1032`, `findings.py:1919`) — output side. A guard there is
  safe, but `open(path,"w")` has already **truncated** the target, so the honest fix is
  write-to-temp-then-`rename`, and both sibling importers read a truncated file as valid
  (`import_markdown` `continue`s on any non-matching line; `_cmd_import_csv` reports "Imported N").
  Turning the traceback into one quiet line without that is a different, larger change.
- **`_cmd_import_csv`'s mid-loop read failure** — `csv.DictReader` iteration is interleaved with
  per-row `add_finding` commits, so an EIO mid-file has rows already landed. What to *report* then
  (atomic rollback vs explicit partial success) is a semantics decision. Revision 1 parked this on
  CB-51; CB-51's text covers identity/restore defects and never mentions I/O, so that was a wrong
  assignment. Its own card, cross-referenced.
- **The post-success output-failure family, package-wide** — see "What this tree deliberately does
  not fix".
- **`OSError` reaching a handler from outside the file-open shape** — `reqs.verify_requirements`
  (`reqs.py:442`) and three `provenance.py` sites call `os.getcwd()`, which raises
  `FileNotFoundError` from a deleted cwd; `provenance.resolve_trailers` (`provenance.py:225`)
  catches `(SubprocessError, FileNotFoundError)` but not `PermissionError`. Reproduced by the
  adversary; different seam, own card.

## Reproducers — all run against `bc3f67e`

Setup: `codebugs init .` in an empty dir; `printf 'label,metric\na,1\n' > good.csv`.
`CB=/home/faxik/w/codebugs/.venv/bin/codebugs` (there is no `codebugs` file in the repo — revision 1
wrote an unrunnable command).

| # | Command | Today | Site | In this tree? |
|---|---|---|---|---|
| 1 | `$CB bench-import missing.csv -b Q` | `FileNotFoundError` traceback | `bench.py:747` | yes (row 1) |
| 2 | `$CB bench-import <a-directory> -b Q` | `IsADirectoryError` traceback | same site | yes (row 1) |
| 3 | `$CB reqs-import missing.md` | `FileNotFoundError` traceback from `reqs.py:536` | `reqs.py:1022` | yes (row 2) |
| 4 | `$CB reqs-export /nonexistent-dir/x.md` | `FileNotFoundError` traceback | `reqs.py:1032` | no — filed |
| 5 | `$CB import-csv missing.csv` | `FileNotFoundError` traceback | `findings.py:1790` | yes (row 3) |
| 6 | `$CB export-csv /nonexistent-dir/x.csv` | `FileNotFoundError` traceback | `findings.py:1919` | no — filed |
| 7 | **post-commit failure laundered as input error** — see below | one clean line, exit 1, **run landed** | `bench.py:754-757` | yes (row 1) |

## Reproducer 7 — the live violation of this plan's own rule, at the site being edited

The pre-existing arm at `bench.py:755` is `except (ValueError, json.JSONDecodeError)` and it **spans
the success `print` at `:754`**, which runs after `import_csv` committed (`bench.py:164`). Any
post-commit `ValueError` from that statement is therefore laundered into an input error. Measured:

```
$ python -c "import sys; sys.argv=['codebugs','bench-import','good.csv','-b','CLOSED_OBJ']
             from codebugs.cli import main; sys.stdout.close(); main()"
stderr: I/O operation on closed file.        <-- one tidy line, no traceback
exit:   1
$ $CB bench-list
CLOSED_OBJ  1  2026-08-17                    <-- THE RUN LANDED
```

Exit 1 and a clean diagnostic for an import that committed: the CB-15/CB-16 success-shaped lie,
live, in the five lines this card edits. **Revision 1 spent twenty lines reasoning about this exact
failure mode for `OSError` and never noticed `ValueError` reaches the same `print` through the same
`try` — and then explicitly rejected hoisting the print, which is its fix.** Hoisting is therefore
row 1's first change, not a rejected alternative.

Two precision corrections to the adversary's version of this finding, both measured, because the
mechanism decides what the test can use:

- Closing **fd 1** (`1>&-`) does *not* reproduce it. Unbuffered gives `OSError: [Errno 9]` from
  `print` → traceback, exit 1 (not laundered — `OSError` is not in the arm). Buffered gives
  `Exception ignored on flushing sys.stdout` at interpreter shutdown, exit 120, outside every
  handler.
- Only closing the **`sys.stdout` object** raises `ValueError: I/O operation on closed file.`, which
  the arm catches. So reachability needs an in-process embedder, not shell redirection — narrower
  than the adversary implied, and the reason the finding is SERIOUS-in-practice rather than a
  user-facing bug. It is still fixed here: the fix is one line, and it is what makes the invariant
  testable.

## What this tree deliberately does not fix

**The post-success output-failure family.** After row 1, `bench-import good.csv -b P | true` still
emits a traceback (unbuffered) or exits 120 (buffered) *after a successful import*, and every
print-and-exit handler in the package shares this. That is now the **correct** behaviour under this
plan's rule — a post-commit failure must surface as a crash — but it is still ugly, and making it
POSIX-clean (`exit 0` under SIGPIPE semantics, or the `dup2(devnull)` shutdown dance) is a
**product decision** applied once at the `cli.main` boundary (`cli.py:141-155`), not per handler.
Filed with the measurements.

**Not a shared boundary helper.** Three per-site arms is three more copies of the arm CB-55 tracks
as copy-maintained, and a helper spans three modules (CB-66's exposure-layer RFC). Note the honest
version of this: a central boundary **already exists** at `cli.py:141-155`, and `OSError` must
**not** go there — reproducer 7 and row 3's stderr prints are both cases where a central arm would
catch a post-write failure. Evidence appended to CB-55 rather than asserted as done.

**CB-75** — `import_csv`'s domain-level `TypeError` for non-`str` `csv_data`. Type validation →
`ValueError` in a domain function is a different transformation in a different layer, so predicate 2
fails. Both reviewers independently affirmed this split.

## Root cause, per row

- **Row 1.** The handler *has* a `try` whose arm covers neither the failure it performs (`OSError`
  from the read) nor safely excludes the statement that runs after the commit. Two defects, one
  five-line region.
- **Row 2.** `_cmd_reqs_import` (`reqs.py:1020-1024`) has **no `try` at all** and no `finally`, so it
  both tracebacks and leaks its connection.
- **Row 3.** `with open(args.file, newline="") as f:` at `findings.py:1790` **owns the entire import
  loop through `:1896`**, so there is no arm placement that guards "the open only" without hoisting
  the `open` out of the `with` — revision 1 stated the requirement with no implementation shape, and
  the naive form (`try: with open(...): <whole loop>`) would put the loop's three `print(...,
  file=sys.stderr)` calls (`findings.py:1866`, `:1877`, `:1886`) *and* already-committed rows inside
  the guard. That is the same lie this plan exists to prevent, so the hoist is mandatory, not
  stylistic. `conn.close()` at `:1898` is also not in a `finally`, and the new exit path makes that
  leak reachable on the very path being fixed (corroborated by both reviewers).

## Verification — the part revision 1 got structurally wrong

The harness is **subprocess-based**, so `"Traceback" not in stderr` is genuinely observable:
`tests/test_bench.py:424-430` and `tests/test_findings.py:759-764` both run
`subprocess.run([sys.executable, "-m", "codebugs.cli", …], capture_output=True, text=True)`, and
there are zero in-process `_cmd_*` calls and zero `capsys` uses in `tests/`. Codex's vacuity worry
about in-process tests does not fire. **But `tests/test_reqs.py` has no subprocess helper, no
`codebugs.cli` reference and no `tmp_project` fixture**, so row 2 needs that harness built — which
revision 1's "`pytest tests/test_reqs.py -q`" silently assumed away.

Three assertion classes, because the first alone cannot see this plan's actual rule:

1. **Missing-path tests** (reproducers 1, 2, 3, 5): exit 1, the path/errno text on stderr, and
   `"Traceback" not in stderr`. Only the third discriminates — the unfixed tree already exits 1 and
   its traceback already contains the path. The first two are kept because they pin the contract
   jointly.
2. **The invariant test, which revision 1 had no equivalent of.** Every test in class 1 passes
   *identically* for the localized guard this plan mandates and for the handler-wide guard it calls a
   CB-15 lie, so class 1 cannot fail when the load-bearing half is broken. The repo already owns the
   template — `tests/test_findings.py:818-822` asserts `"Traceback" **in** r.stderr` precisely so a
   post-commit failure is not laundered. Mirror of it, using reproducer 7's mechanism: close
   `sys.stdout`, run `bench-import`, then assert **the run landed** AND `"Traceback" in stderr` AND
   the clean one-liner is absent. Fails on `bc3f67e` (laundered today), passes after the hoist.
3. **A premise test for row 2** (`WEAKNESS-1`): the whole-call guard is safe only because
   `import_markdown` does `f.readlines()` eagerly at `reqs.py:537` before any write and commits at
   `:606`. Convert that to lazy iteration — the natural memory fix for a large `REQUIREMENTS.md` —
   and the guard silently becomes the post-commit lie with nothing failing. Pin the premise so that
   change turns the suite red, in the style of
   `test_premise_merge_head_is_absent_on_a_clean_merge`.

**Non-vacuity procedure, executable.** `git show main:<file>` is not importable by a subprocess test,
so revision 1's stated method could not have been run. The real method, per `bugfix-loop`
SKILL.md:283-286: commit the fix, then `git stash push` / materialize the pre-fix file over the
tree, re-run, **confirm the mutation landed** (an unmatched patch yields a vacuous "didn't fail"),
then restore. Recorded per test in the commit body.

## Risks

- **Arm interaction.** `json.JSONDecodeError` and `UnicodeDecodeError` subclass `ValueError`;
  `FileNotFoundError`, `IsADirectoryError`, `BrokenPipeError` subclass `OSError`;
  `sqlite3.OperationalError` subclasses neither. Verified. So a new `except OSError` cannot shadow or
  be shadowed by the existing `ValueError` arms, and undecodable bytes keep their current clean
  handling.
- **Hoisting the success print changes an exit path**: a post-commit `ValueError` from `print` goes
  from "exit 1, clean line" to "traceback, exit 1". That is the intended direction (crash, not input
  error) and is what test class 2 pins. Exit code is unchanged at 1.
- **Exit codes are otherwise unchanged** on every path; only stderr content moves from traceback to
  one line.
- **The sweep's method was insufficient and is restated.** `grep -rn --include='*.py' -E
  '(^|[^.a-z_])open\(' src/codebugs/` finds exactly 5 bare `open(` sites and cannot see
  `Path.read_text` (`db.py:605`, `:634` — already guarded), `os.makedirs` (`db.py:1006`),
  `os.getcwd` (`reqs.py:442`, `provenance.py`), `sqlite3.connect`, or `subprocess`. "No sixth
  file-open" was true for that spelling and was wrongly stated as `OSError` completeness. The
  uncovered sites are filed, not implied to be absent — **sweep for the shape, not the names**, this
  repo's own recurring lesson.
