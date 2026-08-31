# Rationale — code-rules

Biography for the corresponding rules in `CLAUDE.md`: review rounds, reproduced incidents,
rejected forms and the measurements a decision was made on. **No rule lives here.** A line
in this file that reads as an instruction is a defect, and its place is the rules layer.

---

### Error handling — CB-79, CB-99, CB-159 {#обработка-ошибок}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### Error handling`.

**Why the ordering pin needed a second test at a different grain (CB-159).** The paragraph once named
only the end-to-end pin, leaving the wrapper itself unexercised. The ordering was then measured
against its exact mutant: removing the `except json.JSONDecodeError: raise` arm turns
`test_a_committed_write_is_never_reported_as_bad_input` red while 5 of that class's other 6 tests are
unaffected — which is what "the ordering is load-bearing" means concretely.

**The two holes CB-71's `open(` sweep structurally could not see, both reproduced (CB-79).**
`reqs-verify` from a **deleted cwd** printed a raw `FileNotFoundError` — a long-lived MCP server
outlives the worktree it started in. And a **non-executable git** raised `PermissionError` out of
`provenance.file_status`, whose guard caught only `FileNotFoundError`, i.e. *git is missing* and
nothing else.

**A negative result worth not re-deriving.** A `chmod 000 git` placed *earlier* on `PATH` does not
reproduce the PermissionError: CPython's exec continues the `PATH` search on `EACCES` and finds the
real git, so the non-executable one must be the only one on `PATH`.

**The CB-99 measurement.** With a simulated `SQLITE_FULL`, `reqs.import_markdown` returned
`{'imported': 0, 'skipped': 2}`, raised nothing, and printed `Imported 0 requirements, skipped 2.` at
exit 0.

**The `skipped` correction.** An earlier draft of that bullet read "`skipped` stays 0", and it was
wrong in a user-facing way — measured, a two-column row plus a full row gives
`{'imported': 1, 'skipped': 1}`.

**The whole-package sweep for this shape** (`grep -rn "except sqlite3\." src/`) found **one**
instance, worth recording precisely because this repo's usual answer is "the population is larger
than the list".

---

### MCP tool registration — CB-28, CB-73 {#регистрация-mcp-инструментов}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### MCP tool registration`.

**Check for an existing design before concluding the clean fix is infeasible (CB-28).** The shape the
fix needed was already specified in `docs/2026-04-04-blockers-design.md:278-291`, and
`get_deferred_item_ids` was already written for it; the wrappers just never used it, and
`provenance.check_findings`' docstring had promised it all along. The first plan here proposed
refusing at every site, and cross-model review showed that was a cheaper substitute for a fix the
repo had already designed.

**The CB-73 measurement, on both interpreters.** 64 of 68 descriptions differed between 3.13 and the
older hosts, and 61 of 68 carried the indented-code-block pattern; both counts are 0 after the fix,
and 3.13 output is byte-identical before and after — which is exactly why the wire golden did not
move.

---

### CLI — CB-48, CB-76, CB-78, CB-134 {#cli-и-выход-процесса}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### CLI`.

**Why `main` must stay signal-free, reproduced.** `tests/test_fsio.py`, `tests/test_findings.py` and
`tests/manual/repro_cb76_truncation.py` call `main` in-process; with the disposition installed there,
`pytest -q -s . | head -2` dies at 141 mid-suite.

**The four cells CB-134 measured, on 3.13.3 and 3.14.4, one mutating verb, two spellings of
"closed".** `sys.stdout.close()` gives **exit 1 + a raw traceback** on both, but the write **lands on
3.13 and not on 3.14** — 3.14's argparse touches stdout while the parser is being BUILT
(`add_argument` → `_get_validation_formatter` → `_colorize.can_colorize` → `os.isatty(file.fileno())`,
and `can_colorize` guards only `OSError` while a closed object raises `ValueError`). `fd 1` closed at
exec gives **120** with "Exception ignored on flushing sys.stdout" on 3.13 and **0, silently, with
the write landed** on 3.14.

**Why the interpreter range needed a real measurement (CB-135).** The claim used to read "every
interpreter `requires-python` admits". Every subprocess in `tests/test_cli_signals.py` is spawned
with `sys.executable`, so the suite measures the one interpreter it runs under, and before the pin
the range was covered only by different people happening to run different versions. Pin that variable
and nobody ever runs the others again — a claim about a range, held up by an accident that had just
been removed. The `contracts` matrix replaces the accident with a measurement: 38 tests, ~1.7s per
version.

**A sentence corrected against itself.** The first draft claimed `sys.stdout = None` before
`sys.exit` was load-bearing; no test could discriminate it, so it is now recorded as insurance whose
deleting mutant survives. The 3.13 fd-closed cell's 120 is where the mechanism was actually observed.

**Where the fd-1 read-only file comes from.** With fd 1 closed at exec, CPython's startup opens
`/sys/kernel/mm/transparent_hugepage/enabled` onto the lowest free descriptor.

**The predicate's first draft claimed more than it held**, and cross-model review rejected it for
that.

**The CB-76 measurement.** A 34-byte export ends at 0 bytes on a simulated `ENOSPC`, and the
`OSError` escaped as a raw traceback besides. `import_markdown` (`reqs.py:564-566`) is the second
half of the round trip.

**A false reason an earlier draft gave for the ordering.** `mkstemp(dir="")` does **not** fail — it
creates in the cwd (measured). The real reason the fd-directory test runs first is the two
resolutions of `/dev/stdout`.

**An enumeration failure committed inside the bullet that cites it.** An earlier draft of the
narrowings sentence counted two and lumped sockets in with block devices; there are three.

**The ratchet's own first draft grepped source text** and matched `open(path, "w")` inside three of
`fsio.py`'s own docstrings.

**CB-48: what the bullet used to say.** "`init` creates where you stand, and a declared root
redirects only reads" — which flattened two channels `db.declared_tracker_root()` already tells
apart. **The defect that fixed was worse than the ignored flag itself**: the warning fired on the path
where the flag had been dropped, so it printed "commands will read DIR, not CWD" immediately *after*
initializing CWD — two adjacent lines asserting the opposite of what was on disk.
