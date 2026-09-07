# Rationale — code-rules

Biography for the corresponding rules in `CLAUDE.md`: review rounds, reproduced incidents,
rejected forms and the measurements a decision was made on. **No rule lives here.** A line
in this file that reads as an instruction is a defect, and its place is the rules layer.

---

### Error handling — CB-79, CB-99, CB-159 {#обработка-ошибок}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### Error handling`.

**`_cmd_reqs_update` was the last asymmetry and was closed by merge `7e46180` (T-57).**

**Why the ordering pin needed a second test at a different grain (CB-159).** The paragraph once named
only the end-to-end pin, leaving the wrapper itself unexercised. The ordering was then measured
against its exact mutant: removing the `except json.JSONDecodeError: raise` arm turns `test_a_committed_write_is_never_reported_as_bad_input` red while all 5 of that class's other tests are
unaffected — which is what "the ordering is load-bearing" means concretely (`TestRetriageCliContract`
carries 6 tests in total, this one included; CB-299).

**The two holes CB-71's `open(` sweep structurally could not see, both reproduced (CB-79).** `reqs-verify` from a **deleted cwd** printed a raw `FileNotFoundError` — a long-lived MCP server outlives the worktree it started in. And a **non-executable git** raised `PermissionError` out of `provenance.file_status`, whose guard caught only `FileNotFoundError`, i.e. *git is missing* and
nothing else.

**A negative result worth not re-deriving.** A `chmod 000 git` placed *earlier* on `PATH` does not reproduce the PermissionError: CPython's exec continues the `PATH` search on `EACCES` and finds the real git, so the non-executable one must be the only one on `PATH`. **The CB-99 measurement.** With a simulated `SQLITE_FULL`, `reqs.import_markdown` returned `{'imported': 0, 'skipped': 2}`, raised nothing, and printed `Imported 0 requirements, skipped 2.` at
exit 0.

**The `skipped` correction.** An earlier draft of that bullet read "`skipped` stays 0", and it was
wrong in a user-facing way — measured, a two-column row plus a full row gives
`{'imported': 1, 'skipped': 1}`. **The whole-package sweep for this shape** (`grep -rn "except sqlite3\." src/`) found **one**
instance, worth recording precisely because this repo's usual answer is "the population is larger
than the list".

---

### MCP tool registration — CB-28, CB-73, CB-326 {#регистрация-mcp-инструментов}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### MCP tool registration`.

**Check for an existing design before concluding the clean fix is infeasible (CB-28).** The shape the
fix needed was already specified in `docs/2026-04-04-blockers-design.md:278-291`, and `get_deferred_item_ids` was already written for it; the wrappers just never used it, and `provenance.check_findings`' docstring had promised it all along. The first plan here proposed
refusing at every site, and cross-model review showed that was a cheaper substitute for a fix the
repo had already designed.

**The CB-73 measurement, on both interpreters.** 64 of 68 descriptions differed between 3.13 and the
older hosts, and 61 of 68 carried the indented-code-block pattern; both counts are 0 after the fix,
and 3.13 output is byte-identical before and after — which is exactly why the wire golden did not
move.

**The FOURTH refusal channel, and why it turned out to be two channels rather than one (CB-326).**
The project's error model described what happens INSIDE a tool and said nothing about what happens on
the way IN. Measured 2026-09-07 through a live client session on a really-built server, under both
admitted `mcp` versions and byte-identically on the two: a call omitting a required field came back
as an error result reading `Error executing tool add: 1 validation error for addArguments / severity
/ Field required [...] / For further information visit https://errors.pydantic.dev/2.12/v/missing` —
another party's phrasing, the name of an internal model, and a link to a third-party site, in a
tracker whose every other refusal speaks its own words. Not a rare path: this tracker's own usage
table recorded `add` refusing 65 of 591 calls, the highest share of any core tool.

**The revision document asked for an experiment on both versions because it expected them to differ,
and they do not.** All four measured shapes are byte-identical; the only difference is that 2.1.1
writes an extra line to the server's stderr (`Tool 'add' rejected arguments: ['severity']`), which no
client sees. Checking both remains worthwhile as a CHECK — it is cheap, and a divergence could appear
where none is today — but expecting one wastes the reader's attention.

**The boundary is a PAIR, and the halves do not reduce to each other.** Schema validation by the
type library yields a RESULT carrying a foreign text; `install_strict_arguments` yields a PROTOCOL
error carrying the project's text, which a client library RAISES rather than returns. Two different
failure texts and two different things a caller must write to handle them.

**The cheap fix was measured and rejected, which is the finding worth keeping.** Rewriting the error
result on the way out is possible — a middleware really does see it, and a substituted text reaches
the client verbatim, both measured. But a DOMAIN refusal arrives in the identical shape, so a
rewriting layer could only tell the two apart by matching the validation library's own wording:
taking a dependency on precisely the thing the card exists to remove, and one that would fail
silently the day that library rephrased itself. Asking the tool's own declared schema which arguments
are required needs no such match. Hence a check AHEAD of the call.

**Placement was decided by a measurement, not by taste, and it is why this is a second middleware
rather than a branch in the first.** The two checks need OPPOSITE positions relative to
`install_usage_tracking`. An unknown argument NAME is refused outside it and deliberately goes
uncounted. A missing required field is counted TODAY — measured, one call, `add: calls=1 failures=1`
— because the SDK catches it deeper than the usage layer reaches. Installing the new layer outside
usage tracking would have silently stopped counting those calls, and the evidence justifying the card
was read out of that very table. One function cannot occupy both positions, so there are two.

**What was deliberately NOT taken on.** A wrong argument TYPE still answers in the library's words.
Deciding it here means a second schema validator beside the real one; validators drift, and this one
would drift toward refusing values the tool would have accepted — the expensive direction. The
answer's SHAPE was also left alone: the refusal stays a result rather than becoming a protocol error,
because merging the two channels would change how every existing caller must handle it and buys
nothing, the card being about the words and not the channel.

**How the client-visible contract was held.** `tests/manual/snapshot_cb326_error_contract.py` records
eight protocol scenarios, a usage-counting section and a CLI section, under each version, before and
after. The before/after diff is exactly the two subject scenarios; the six controls, the whole usage
section and the whole CLI section are byte-identical, and the two versions still agree byte for byte
after the change. This was needed because the wire golden is not a gate on the response FORM — no
`outputSchema` is snapshotted and the live schema carries `additionalProperties: True`.

**One mutation probe corrected the tests rather than the code.** Asserting "the text contains
`severity`" survived a mutant that dropped the list of MISSING fields while keeping the list of
REQUIRED ones, because the required list contains every name anyway. The replacement compares the
answers to a one-missing and a two-missing call and requires them to differ — format-independent, so
it survives rewording, and still fatal to the loss of content.

**A pre-existing false sentence found on the way, and why nothing had caught it.** The rule bullet
read "This is the one place the project touches `MCPServer.middleware`". That had already become
false when `install_usage_tracking` landed, and `tests/test_claude_md_truth.py` could not see it: its
declared vocabulary omits the word `one`, because in this prose `one` is almost always a pronoun.
The blind spot is documented in the root rules file; this is an instance of it doing real harm.

---

### CLI — CB-48, CB-76, CB-78, CB-134 {#cli-и-выход-процесса}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### CLI`. **Why `main` must stay signal-free, reproduced.** `tests/test_fsio.py`, `tests/test_findings.py` and `tests/manual/repro_cb76_truncation.py` call `main` in-process; with the disposition installed there, `pytest -q -s . | head -2` dies at 141 mid-suite.

**The four cells CB-134 measured, on 3.13.3 and 3.14.4, one mutating verb, two spellings of
"closed".** `sys.stdout.close()` gives **exit 1 + a raw traceback** on both, but the write **lands on
3.13 and not on 3.14** — 3.14's argparse touches stdout while the parser is being BUILT
(`add_argument` → `_get_validation_formatter` → `_colorize.can_colorize` → `os.isatty(file.fileno())`, and `can_colorize` guards only `OSError` while a closed object raises `ValueError`). `fd 1` closed at
exec gives **120** with "Exception ignored on flushing sys.stdout" on 3.13 and **0, silently, with
the write landed** on 3.14.

**Why the interpreter range needed a real measurement (CB-135).** The claim used to read "every
interpreter `requires-python` admits". Every subprocess in `tests/test_cli_signals.py` is spawned with `sys.executable`, so the suite measures the one interpreter it runs under, and before the pin
the range was covered only by different people happening to run different versions. Pin that variable
and nobody ever runs the others again — a claim about a range, held up by an accident that had just
been removed. The `contracts` matrix replaces the accident with a measurement: 38 tests, ~1.7s per
version.

**A sentence corrected against itself.** The first draft claimed `sys.stdout = None` before `sys.exit` was load-bearing; no test could discriminate it, so it is now recorded as insurance whose
deleting mutant survives. The 3.13 fd-closed cell's 120 is where the mechanism was actually observed.

**Where the fd-1 read-only file comes from.** With fd 1 closed at exec, CPython's startup opens
`/sys/kernel/mm/transparent_hugepage/enabled` onto the lowest free descriptor.

**The predicate's first draft claimed more than it held**, and cross-model review rejected it for
that.

**The CB-76 measurement.** A 34-byte export ends at 0 bytes on a simulated `ENOSPC`, and the `OSError` escaped as a raw traceback besides. `import_markdown` (`reqs.py:564-566`) is the second
half of the round trip.

**A false reason an earlier draft gave for the ordering.** `mkstemp(dir="")` does **not** fail — it
creates in the cwd (measured). The real reason the fd-directory test runs first is the two
resolutions of `/dev/stdout`.

**An enumeration failure committed inside the bullet that cites it.** An earlier draft of the
narrowings sentence counted two and lumped sockets in with block devices; there are three.

**The ratchet's own first draft grepped source text** and matched `open(path, "w")` inside three of `fsio.py`'s own docstrings. **CB-48: what the bullet used to say.** "`init` creates where you stand, and a declared root redirects only reads" — which flattened two channels `db.declared_tracker_root()` already tells
apart. **The defect that fixed was worse than the ignored flag itself**: the warning fired on the path
where the flag had been dropped, so it printed "commands will read DIR, not CWD" immediately *after*
initializing CWD — two adjacent lines asserting the opposite of what was on disk.

---

### Testing — CB-204, CB-215 {#тестирование}

**Justifies the rules** in `CLAUDE.md` → `## Code rules` → `### Testing`.

**Why the conftest rule stopped being a count (CB-204).** It read *"exists for exactly one thing and
should stay that way"*, and had to be rewritten the first time a second qualifying property
appeared.

**The ambient-state fixture was verified, not theorized.** With `CODEBUGS_ROOT` exported, the findings CLI tests moved a real CB-1 from `low`/`open` to `high`/`fixed` in the developer's own
tracker.

**What the CB-204 session guard is worth, measured 2026-08-26 by running it.** With an empty
`.codebugs/` directly above the temporary root, **1071 of 2739 tests** fail or error. After the guard,
that same state is one refusal in 0.7s at exit 4.

**Why the CB-215 alarm exists, measured on main's own history.** The median gap between first-parent
commits is 141 seconds against a suite run of about 170, so a merge arriving mid-run is an ordinary
Tuesday.

**Silence on a still tree was measured** over the full suite: 2878 tests, nothing printed.

## Что в этом файле, и чего в нём нет

**Что в этом файле.** Обоснования правил из корневого `CLAUDE.md`: почему правило появилось, какой
инцидент его породил, что показали раунды состязательного ревю, какие формы были отвергнуты и по
какому замеру. С T-131 сюда же переехала операционная глубина — устройство сторожей и хуков,
пределы алярмов, внутренности гейтов.

**Чего в этом файле НЕТ, и это важнее.** Здесь нет ни одного правила, которое нужно знать до начала
работы. Всё такое осталось в корневом `CLAUDE.md`, потому что этот файл не впрыскивается в сессию —
его открывает только тот, кого сюда послали. Если ты ищешь, как завести рабочее дерево, что значит
код отказа или что можно коммитить на `main`, — тебе не сюда, а в корень.

**Кто сюда ходит.** Тот, кто правит соответствующую подсистему, — и тот, кто собирается ослабить
правило и обязан сперва узнать, чем за него заплатили.

---

# Перенесено из корня юнитом T-131

## Code rules / Error handling

Reporting that as bad input prints a tidy one-line error and exits 1 for a mutation that **already landed** — a failure-shaped signal for a successful write, the same class of lie as CB-15/CB-16. `_cmd_reqs_update` was the last asymmetry and is closed (T-57).

What the split rests on is that CPython routes every environmental code to `OperationalError`, so nothing environmental is inside the arm; a test pins that a CHECK violation on `requirements` really is an `IntegrityError`, as a premise rather than an argument. **No classifier is involved, and that is better than reusing `_is_environmental`**: the exception TREE already draws this line, so reaching for a predicate would mean exporting a deliberately private one or growing a second copy of its enumeration. 

## Code rules / Testing

**Three things about it are load-bearing and each was measured.** *The walk is asked, never re-implemented*: a parent climb to `/` would falsely alarm on a tracker above a `.git` DIRECTORY (the walk stops there) and would MISS one reachable only by following a `.git` FILE to a linked worktree's main checkout (the walk jumps) — both are oracle rows, and a structural pin fails if the delegation is replaced. *The start point comes from the factory the `tmp_path` fixture is built on*, not from the literal `/tmp`: `--basetemp` and `TMPDIR` both move it, so a hardcoded `/tmp` would be a gate that cannot fire. It exists because the suite is re-run by an acceptor **in the main checkout**, which is exactly where other directions land their branches, while structural tests here read source files from disk, so a merge arriving mid-run is ordinary and the partial red it produces is indistinguishable from a regression. *The discriminator is the FILES, not `HEAD`*, measured: `git rev-parse` fails outright in a tree unpacked without a git directory, does not move in a worktree when `main` moves (the case that must stay silent), and cannot see an editor or a formatter writing a file nobody committed; the commit name is printed as a SIGNATURE when git answers, and its absence is never a failure. *Nothing is pruned by judgement* — `.claude/plans/` is deliberately watched, because `tests/test_exposure_matrix.py` really does read `.claude/plans/exposure-scripts/matrix.py` off the real tree, so *"the suite does not look there"* is precisely the unchecked premise the alarm exists to stop people acting on; the two prune tables hold only what is not a source of anything (git's own directory, the virtual environment, the two worktree directories, the tracker, and caches), each with the sentence saying why, and **a bare list with no reasons becomes the place inconvenient paths are hidden**. 

## Code rules / MCP tool registration

Without it the SDK builds each tool's argument model with pydantic's default `extra="ignore"`, so a typo'd name is dropped during validation and the tool returns a **success payload with the caller's data discarded** — while a bad *value* raises (CB-15). **`additionalProperties: false` is not an alternative**: the server never validates arguments against the JSON Schema, verified by injecting it and watching the call still succeed. 

**Two alternatives were rejected for reasons worth keeping**: rewriting `fn.__doc__` is a global side effect on another module's objects, and rewriting the registered `Tool` objects afterwards reaches into the SDK's PRIVATE `_tool_manager._tools` — a worse coupling than the provisional-but-public one `install_strict_arguments` already documents. 

## Code rules / CLI

A dead READER on stdout otherwise makes every verb report a **committed** write as a failure — exit 1 with a `BrokenPipeError` traceback unbuffered, and exit 120 with "Exception ignored on flushing sys.stdout" block-buffered, the latter raised at interpreter shutdown where no `except` can reach it. 

**The dangerous case is the newest**: on 3.14, an invalid fd 1 makes `sys.stdout` `None`, `print` is a documented no-op against `None`, and the colour probe short-circuits on `hasattr(None, "fileno")` — so every verb runs, discards its whole output and **reports success**. That is the "silent exit 0" CB-78's ratification rejected by name, reached by upgrading the interpreter rather than by changing any code here: `codebugs export-csv /dev/stdout | gzip > backup.gz` reports success over a backup that was never written. 

A test that asserts only "the target got a tracker" cannot see the defect this fixed; `TestInitUnderTheTrackerRootFlag` asserts the directory that must **not** have one on every case.
