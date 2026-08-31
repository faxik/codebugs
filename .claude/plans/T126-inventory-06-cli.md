# Inventory — CLAUDE.md lines 914–952 (Error handling / Testing / MCP tool registration / CLI)

L915 | RULE | Domain functions raise `ValueError` for invalid input and `KeyError` for missing entities.
L916 | RULE | MCP tools let exceptions propagate to the MCP server's built-in error handling.
L917 | RULE | CLI handlers catch domain exceptions and print to stderr with `sys.exit(1)`.

L918 | RULE | A failure raised AFTER the commit must never be reported through the input-validation arm.
L918 | EXAMPLE | A domain update commits its write and only then can raise while serializing the return value from a row with malformed stored `meta`/`tags`.
L918 | BOUNDARY | Reporting that as bad input prints a tidy one-line error and exits 1 for a mutation that already landed — a failure-shaped signal for a successful write, the same class of lie as CB-15/CB-16.
L918 | RULE | The rule is encoded exactly once, in `cli.domain_errors()`.
L918 | RULE | Every CLI handler that touches a domain call routes it through `with domain_errors():` rather than catching exceptions itself.
L918 | RULE | Its two `except` arms must stay in this order: `json.JSONDecodeError` re-raises unchanged FIRST, and only THEN does a plain `(ValueError, KeyError)` arm print one line and `sys.exit(1)`.
L918 | WHY-NARROW | Reversing or collapsing the two loses exactly this distinction, because `json.JSONDecodeError` IS a `ValueError` subclass — enumerate what subclasses a widened catch before trusting it.
L918 | IDENT | `tests/test_findings.py::TestDomainErrorsOrderingPin` exercises `domain_errors()` directly, in isolation (CB-159).
L918 | HISTORY-LOADBEARING | CB-159 — an earlier version of this paragraph named only the end-to-end pin below, with nothing exercising the wrapper on its own.
L918 | IDENT | `TestRetriageCliContract::test_a_committed_write_is_never_reported_as_bad_input` re-confirms the ordering through the real `update` CLI verb and a corrupted database.
L918 | MEASURED | Measured against this exact mutant, twice: removing the `except json.JSONDecodeError: raise` arm turns that specific test red — 5 of the class's other 6 tests are unaffected, which is what "the ordering is load-bearing" means concretely.
L918 | RULE | `_cmd_query` and `_cmd_reqs_query` carry the same ordering, added when their vocabulary filters began resolving (CB-19).
L918 | HISTORY-LOADBEARING | Until then neither caught `ValueError` at all, so an unknown `--status` printed a raw traceback and leaked the connection — a handler that catches nothing violates the rule just as surely as one that catches in the wrong order.
L918 | HISTORY | `_cmd_reqs_update`'s asymmetry is closed (T-57, merge `7e46180`): it now routes through the same `domain_errors()` wrapper as every other handler, rather than catching `KeyError` alone — this paragraph used to name it as the still-outstanding case.

L919 | RULE | `OSError` arrives from ambient sources, not just from `open()` — a guard spelled as one errno is an enumeration (CB-79).
L919 | HISTORY-LOADBEARING | CB-71 swept for `open(` and closed five sites; that spelling structurally cannot see `os.getcwd()`, `subprocess`, `Path.read_text` or `sqlite3.connect`.
L919 | MEASURED | `reqs-verify` from a deleted cwd printed a raw `FileNotFoundError` (a long-lived MCP server outlives the worktree it started in) — reproduced.
L919 | MEASURED | A non-executable git raised `PermissionError` out of `provenance.file_status`, whose guard caught only `FileNotFoundError` (i.e. "git is missing" and nothing else) — reproduced.
L919 | RULE | All five subprocess guards (`provenance.py` ×4, `db.git_rev_parse`) now catch `OSError` — a strict widening, since `FileNotFoundError` is an `OSError`.
L919 | RULE | `subprocess.SubprocessError` must stay in each tuple because it is NOT an `OSError` subclass, and dropping it loses `CalledProcessError`/`TimeoutExpired`.
L919 | HISTORY-LOADBEARING | Widening a guard can expose a latent wrong answer: the rename lookup swallowed its failure into `rename_output = ""` and the fall-through then reported `deleted` — the "guard reporting clean because it could not look" shape; it returns `unknown` now.
L919 | RULE | Degrade or raise is decided by the CALLER's contract, not by the failure.
L919 | RULE | `provenance` degrades (`file_status` → `unknown`, `_parse_trailers` → `[]`) because that is already what it does when git is unreachable.
L919 | RULE | `db._db_path` raises `DatabaseNotFoundError` because its callers all handle that.
L919 | RULE | `verify_requirements` raises because it has no unknown vocabulary, so a false clean would be the worse answer.
L919 | RULE | Resolve an ambient value where it is USED, not at the top of the function.
L919 | EXAMPLE | `root` is consumed only by the `tests` check, so an eager `os.getcwd()` broke `checks=["ids"]` for a check that never looks at a directory.
L919 | RULE | `_cmd_reqs_verify` needs the `json.JSONDecodeError`-before-`ValueError` ordering like its siblings, because `verify_requirements` calls `db.row_to_dict`.
L919 | MEASURED | A negative result worth not re-deriving: a `chmod 000 git` placed earlier on `PATH` does not reproduce the `PermissionError` — CPython's exec continues the `PATH` search on `EACCES` and finds the real git, so the non-executable one must be the only one on `PATH`.

L920 | RULE | A per-row swallow inside an import loop catches the row-level exception CLASS, never the tree (CB-99).
L920 | HISTORY-LOADBEARING | `reqs.import_markdown` guarded its per-row INSERT with `except sqlite3.Error` — every SQLite exception there is — so an environmental failure arriving mid-import was counted as a malformed ROW and the import reported success.
L920 | MEASURED | Measured with a simulated `SQLITE_FULL`: `{'imported': 0, 'skipped': 2}`, no exception, "Imported 0 requirements, skipped 2." at exit 0.
L920 | BOUNDARY | Strictly worse than the traceback CB-86 removes, because a traceback is loud.
L920 | RULE | The narrowing is to `sqlite3.IntegrityError`, the class for a row that violates the table's constraints.
L920 | WHY-NARROW | Written as that sentence and deliberately NOT as a list of codes, because review measured the list wrong in both directions (`SQLITE_MISMATCH` is also an `IntegrityError`; `SQLITE_TOOBIG` is a `DataError`, a sibling).
L920 | RULE | The split rests on CPython routing every environmental code to `OperationalError`, so nothing environmental is inside the arm.
L920 | IDENT | A test pins that a CHECK violation on `requirements` really is an `IntegrityError`, as a premise rather than an argument.
L920 | WHY-NARROW | No classifier is involved, and that is better than reusing `_is_environmental`: the exception TREE already draws this line, so reaching for a predicate would have meant exporting a deliberately private one or growing a second copy of its enumeration.
L920 | BOUNDARY | With the resolvers normalising `status`/`priority` before the INSERT and `INSERT OR REPLACE` foreclosing UNIQUE, no parseable markdown row can currently violate a constraint at all — measured, so the arm is a safety net for a future schema, not a live path.
L920 | RULE | Because the commit is at the END of the loop, propagating rather than swallowing means a mid-import failure now lands nothing instead of a partial import reported as success.
L920 | HISTORY-LOADBEARING | Do not read that as "`skipped` stays 0" — an earlier draft of this bullet did, and it was wrong in a user-facing way: `skipped` has a second, live producer in the `len(cells) < 4` guard, reachable by construction because `_ROW_RE` anchors only on the leading id cell.
L920 | MEASURED | Measured: a two-column row plus a full row gives `{'imported': 1, 'skipped': 1}`.
L920 | RULE | It is the INTEGRITY contribution that is expected to stay 0.
L920 | MEASURED | The whole-package sweep for this shape (`grep -rn "except sqlite3\." src/`) found one instance.
L920 | REPEAT | Restates this repo's recurring lesson ("the population is larger than the list") already stated at the CB-24 population bullet elsewhere in the doc.

L921 | RULE | All MCP tools return `dict[str, Any]`.

L924 | RULE | Tests live in `tests/test_<module>.py`.
L924 | RULE | Most test classes use a fresh in-memory DB via a `conn` fixture.
L925 | RULE | Tests requiring `db.connect()`, cross-module schemas, or git operations use `tmp_path` file-based DBs.

L926 | RULE | Each test file defines its own fixtures; `tests/conftest.py` is not a shared-fixture drawer.
L926 | RULE | `conftest.py` admits exactly one KIND of inhabitant — a property that protects the whole suite, whose failure mode is silent or unattributable, and which every future test file would otherwise have to remember for itself.
L926 | RULE | Ordinary fixtures are not that, and still belong in the file that uses them.
L926 | RULE | A safety property whose failure mode is silent corruption must not be an enumeration every future file has to remember, and neither must one whose failure mode is a thousand failures pointing at code that is fine.
L926 | HISTORY-LOADBEARING | CB-204: the rule is now stated as a KIND rather than as a COUNT — it used to read "exists for exactly one thing and should stay that way", which had to be rewritten the first time a second qualifying property appeared, and a count in prose is the thing this document has twice been wrong about.
L926 | RULE | Every inhabitant answers one question in a different place — "what did this run actually judge?" — and that sentence is the property, deliberately not another count.
L926 | HISTORY-LOADBEARING | The clause it replaces was already rewritten once the moment a second qualifying inhabitant appeared, and would have to be rewritten again without this reformulation.
L926 | RULE | The property is asked of the TRACKER by an ambient-state fixture and by the CB-204 session guard, where the failure mode is a test that names one state and gets another because `db.connect()` resolves against ambient state the test never declared.
L926 | RULE | The property is asked of the SOURCE TREE by the CB-215 alarm.
L926 | RULE | The ambient-state fixture clears `CODEBUGS_ROOT` and the tracker-root override, because three modules shell out to the CLI with mutating verbs and a forgotten guard silently rewrites the developer's real tracker.
L926 | MEASURED | Verified, not theorized: with the variable exported, the findings CLI tests moved a real CB-1 from `low`/`open` to `high`/`fixed`.
L926 | RULE | The CB-204 session guard asks the product's own walk (`db._find_db_root`, the single function `_resolve_db` uses for the discovery route, called with an explicit start exactly as `cli.py:91` calls it) whether a `.codebugs/` sits at or above `tmp_path_factory.getbasetemp()`, and refuses the whole session with one named diagnostic if so.
L926 | RULE | The walk is asked, never re-implemented.
L926 | WHY-NARROW | A parent climb to `/` would falsely alarm on a tracker above a `.git` DIRECTORY (the walk stops there) and would MISS one reachable only by following a `.git` FILE to a linked worktree's main checkout (the walk jumps) — both are oracle rows, and a structural pin fails if the delegation is replaced.
L926 | RULE | The start point comes from the factory the `tmp_path` fixture is built on, not from the literal `/tmp`.
L926 | WHY-NARROW | `--basetemp` and `TMPDIR` both move it, so a hardcoded `/tmp` would be a gate that cannot fire.
L926 | RULE | The declared channels (`CODEBUGS_ROOT`, tracker-root override) are deliberately NOT checked by this guard.
L926 | WHY-NARROW | The first fixture already neutralizes them before every test, so refusing on one would be the false alarm that gets a guard deleted by the first person it inconveniences.
L926 | BOUNDARY | What the guard does NOT do: the tests are not hermetic afterwards, they merely stop lying about why they failed.
L926 | MEASURED | Measured 2026-08-26: with an empty `.codebugs/` directly above the temporary root, 1071 of 2739 tests fail or error; after the guard that same state is one refusal in 0.7s at exit 4.
L926 | WHY-NARROW | The CB-215 alarm is an ALARM rather than a guard because by the time the two samples can be compared the run is over, so there is nothing left to refuse.
L926 | RULE | It fingerprints every file in the tree (path, size, `mtime_ns`) before the first test and again in the terminal summary, and prints what differs.
L926 | WHY-NARROW | It exists because the suite is re-run by an acceptor in the main checkout, exactly where other directions land their branches, while structural tests here read source files from disk.
L926 | MEASURED | Measured on main's own history: the median gap between first-parent commits is 141 seconds against a run of ~170, so a merge arriving mid-run is an ordinary Tuesday and the partial red it produces is indistinguishable from a regression.
L926 | RULE | The exit status is never touched, and the message says so in words — a moved tree is ordinary traffic, and refusing over it would manufacture a false red out of noise.
L926 | RULE | The discriminator is the FILES, not `HEAD`.
L926 | MEASURED | `git rev-parse` fails outright in a tree unpacked without a git directory; does not move in a worktree when `main` moves (the case that must stay silent); cannot see an editor or a formatter writing a file nobody committed.
L926 | RULE | The commit name is printed as a SIGNATURE when git answers, and its absence is never a failure.
L926 | RULE | Nothing is pruned by judgement — `.claude/plans/` is deliberately watched.
L926 | WHY-NARROW | `tests/test_exposure_matrix.py` really does read `.claude/plans/exposure-scripts/matrix.py` off the real tree, so "the suite does not look there" is precisely the unchecked premise the alarm exists to stop people acting on.
L926 | RULE | The two prune tables hold only what is not a source of anything (git's own directory, the virtual environment, the two worktree directories, the tracker, and caches), each with the sentence saying why, and a bare list with no reasons becomes the place inconvenient paths are hidden.
L926 | MEASURED | On a still tree it prints nothing at all — not a header, not an empty section; measured over the full suite, 2878 tests, silent.
L926 | BOUNDARY | The same name is pruned as a FILE and as a DIRECTORY by ONE predicate, because `.git` is a directory in the main checkout and a file in every linked worktree, and a rule that answered differently in the two would be the wrong rule for a defect whose whole subject is main-versus-worktree.
L926 | BOUNDARY | What it does NOT do: it cannot stop the race, only report it; the window it covers runs from the first test to the last, and a tree that moved during collection is invisible to it.

L927 | RULE | Test the domain module's public API, not internal helpers.

L928 | RULE | A concurrency test's ASSERTION is the hard part, not its scheduling (CB-27, CB-30).
L928 | HISTORY-LOADBEARING | Three separate drafts in one iteration could not have failed against the unfixed code, which is the failure this repo keeps shipping.
L928 | RULE | (a) Check that the final STATE actually discriminates.
L928 | EXAMPLE | In the `mark_items` race the item ends at `b` both before and after the fix, so the only real discriminator is which writer is refused — capture the competing thread's exception and assert on it.
L928 | RULE | (b) Never wait unboundedly on the losing writer.
L928 | WHY-NARROW | After the fix it blocks at `BEGIN IMMEDIATE` and can never complete, so "let B finish" just burns `busy_timeout`.
L928 | IDENT | Copy the bounded three-event interleave in `tests/test_findings.py:504-547`, whose docstring explains why the `b_started` guard before the 1.0s `b_read` wait is what stops a false pass.
L928 | RULE | (c) To probe a commit seam, hook BOTH seams.
L928 | RULE | Unfixed code closes with `conn.commit()`; `db.txn` closes with `conn.execute("COMMIT")`; a hook keyed on one gives a vacuous pass on the other.
L928 | IDENT | `CommitPausingConnection` in `tests/test_milestones.py` does both, fires after the underlying commit (firing before it leaves the write lock held would deadlock the injecting connection), and is single-threaded — no timing luck.
L928 | RULE | A test that passes on both sides can still be right, but only when it pins behaviour the change deliberately preserved — say so in its name or docstring, or a reader cannot tell it from a broken one.

L929 | RULE | Run tests: `uv run python -m pytest tests/ -v`
L930 | RULE | Run lint: `uv run ruff check src/ tests/`
L931 | RULE | Run format: `uv run ruff format src/ tests/`

L934 | RULE | Each domain module defines `register_tools(mcp, conn_factory)` and calls `register_tool_provider()` at module level.
L935 | RULE | `server.py` discovers providers via the registry and passes `_conn` as the `conn_factory`.
L936 | RULE | Tool parameters that accept JSON should use `str | list | None` (not just `str`) so MCP clients can pass native types.
L937 | RULE | New modules: define `register_tools(mcp, conn_factory)`, call `register_tool_provider("name", register_tools)` at module level.

L938 | RULE | A declared argument must reach its query, or the call must fail — routing is not an excuse (CB-28).
L938 | RULE | This rule covers a known, correctly spelled, correctly typed argument that a branch simply never forwards (the twin of the "unknown argument name" rule stated at L939).
L938 | HISTORY-LOADBEARING | `query(status="deferred", severity="critical")` returned every deferred finding, and the caller read that as the critical ones — same success-shaped lie as an unknown argument, reached through routing instead of validation, and no validation layer can see it.
L938 | RULE | Two different repairs exist, and picking the wrong one is how this becomes a stopgap: forward when a path exists, refuse only when none could.
L938 | RULE | `deferred` is a pseudo-status, so it resolves to an id restriction via `blockers.deferred_ids_and_counts` (since CB-69), which returns the restricted ids and their active-blocker counts from ONE evaluation.
L938 | RULE | The owning domain applies its own filters; blockers never learns what `severity` or `priority` mean.
L938 | IDENT | This shape was already specified in `docs/2026-04-04-blockers-design.md:278-291` and `get_deferred_item_ids` was already written for it; the wrappers just never used it, and `provenance.check_findings`'s docstring had promised it all along.
L938 | RULE | Check for an existing design before concluding the clean fix is infeasible.
L938 | HISTORY | The first plan here proposed refusing at every site, and cross-model review showed that was a cheaper substitute for a fix the repo had already designed.
L938 | RULE | Refusal is right only where nothing could honour the argument: an abandoned milestone item has no `done_commit` column, `set_item_status`'s no-op path performs no write (use `mark_integrated`), and a lone `meta_value` has no key to look up.
L938 | RULE | The empty intersection is the trap: an empty `ids` list means "no filter" to every domain query, so forwarding one returns the whole table — the defect reappearing inside its own fix, as CB-25's naive predicate did inside its own fix (REPEAT of the CB-25 lesson stated elsewhere in the document).
L938 | RULE | Short-circuit to an empty page in that case.
L938 | IDENT | `TestDeferredEmptyIntersection` pins the empty-intersection short-circuit.

L939 | RULE | Unknown argument names are refused, not ignored.
L939 | IDENT | `server.install_strict_arguments()` runs after registration and rejects any `tools/call` carrying an argument the tool does not declare.
L939 | HISTORY-LOADBEARING | Without it the SDK builds each tool's argument model with pydantic's default `extra="ignore"`, so a typo'd name is dropped during validation and the tool returns a success payload with the caller's data discarded — while a bad value raises (CB-15).
L939 | BOUNDARY | `additionalProperties: false` is not an alternative: the server never validates arguments against the JSON Schema, verified by injecting it and watching the call still succeed.
L939 | BOUNDARY | This is the one place the project touches `MCPServer.middleware`, whose signature the SDK documents as provisional — if an upgrade breaks it, repair that function and `tests/test_server.py`, nothing else.

L940 | RULE | What a client SEES must not depend on which interpreter built the server (CB-73).
L940 | MEASURED | The SDK reads `Tool.description` from `__doc__`; CPython 3.13 dedents docstrings at compile time and 3.11/3.12 do not, so on the older hosts `requires-python` admits, clients received the source indentation.
L940 | HISTORY-LOADBEARING | Because MCP clients render descriptions as Markdown, CommonMark turned a 4-space-indented line after a blank line into an indented code block, rendering most tools' whole prose body as monospaced code.
L940 | MEASURED | Measured on both interpreters: 64/68 descriptions differed and 61/68 carried the code-block pattern; both are 0 after the fix.
L940 | MEASURED | 3.13 output is byte-identical before and after the fix — which is exactly why the wire golden did not move.
L940 | IDENT | `server._NormalizedDescriptions` wraps the registrar and passes `description=`, a public, declared parameter of `MCPServer.tool()`.
L940 | BOUNDARY | Two alternatives were rejected: rewriting `fn.__doc__` is a global side effect on another module's objects; rewriting the registered `Tool` objects afterwards reaches into the SDK's PRIVATE `_tool_manager._tools` — a worse coupling than the provisional-but-public one `install_strict_arguments` already documents.
L940 | RULE | An explicit `description=` from a caller still wins, because the adapter normalizes and does not decide.
L940 | RULE | `dedent_docstring` lives in `server.py` and `tests/_mcp_schema` imports it, rather than keeping a second copy, because a second definition would be one drift away from the gate and the server disagreeing about the very thing they exist to keep in agreement.
L940 | HISTORY | While it normalized only the comparison, a test-side copy was harmless; now that the server emits normalized text, a second definition would risk drift.

L941 | RULE | A parameter that exists in the domain layer is not reachable until it is declared at the MCP layer.
L941 | HISTORY-LOADBEARING | `append_note` sat unexposed behind the destructive `notes` for a long time (CB-18).
L941 | RULE | When adding a parameter, update the MCP wrapper, the CLI parser AND handler, then regenerate the wire golden with `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`.
L941 | WHY-NARROW | From a worktree a bare `python` resolves `codebugs` through the editable install pointing at the main checkout and would snapshot the wrong tree, hence the explicit `PYTHONPATH=src`.

L944 | RULE | Each domain module defines `register_cli(sub, commands)` and calls `register_cli_provider()` at module level.
L945 | RULE | `cli.py` discovers providers via the registry and filters by `--mode` flag.
L946 | RULE | New modules: define `register_cli(sub, commands)`, call `register_cli_provider("name", register_cli)` at module level.

L947 | RULE | Two commands are built into `cli.py` rather than owned by a domain module, registered by `_register_builtins`: `init` and `where`.
L947 | RULE | `init` bootstraps the DB every other command needs.
L947 | RULE | `where` diagnoses the case where that DB cannot be found at all.
L947 | WHY-NARROW | Both must work in every `--mode` and before any tracker is reachable, which is exactly what a domain module cannot promise.
L947 | RULE | `--tracker-root` is likewise global: it lives on the `pre_parser`, so it is parsed before subcommand dispatch and binds every verb, not just `where`.

L948 | RULE | The process entry point is `cli.run`, not `cli.main`.
L948 | WHY-NARROW | The split exists so a signal disposition never leaks into an importable function (CB-78).
L948 | RULE | `run` restores the POSIX `SIGPIPE` default and calls `main`; `[project.scripts]` and the `__main__` guard both point at `run`.
L948 | HISTORY-LOADBEARING | Before this, a dead READER on stdout made every verb report a committed write as a failure — exit 1 with a `BrokenPipeError` traceback unbuffered, and exit 120 with "Exception ignored on flushing sys.stdout" block-buffered, the latter raised at interpreter shutdown where no `except` can reach it.
L948 | RULE | `SIG_DFL` fixes both with one line and yields 141 (`128 + SIGPIPE`), deliberately distinguishable from 1; see the exit-code API under Claims module for what that means to shell callers.
L948 | RULE | `main` must stay signal-free because `tests/test_fsio.py`, `tests/test_findings.py` and `tests/manual/repro_cb76_truncation.py` call it in-process.
L948 | WHY-NARROW | Installed there, the disposition is an unrestored process-global mutation and the whole pytest session inherits it.
L948 | MEASURED | Reproduced: `pytest -q -s . | head -2` dies at 141 mid-suite.
L948 | RULE | `run` must never restore the SIGPIPE disposition: doing so in a `finally` puts the block-buffered case back to exit 120, because that write is the shutdown flush and happens after `main` returns.
L948 | RULE | "Do not mutate process-global state" and "fix the block-buffered case" are incompatible inside one function — the split is what makes both true.
L948 | RULE | `server.main` is deliberately excluded (its stdout is the JSON-RPC transport), and says so at the call site.
L948 | BOUNDARY | Costs, stated because they are real: `export-csv /dev/stdout` and `reqs-export` into a dead reader lose their one-line diagnostic, and an install predating the change keeps the old behaviour until `pipx reinstall` regenerates the console shim.

L949 | RULE | A CLOSED stdout is a different state from a dead reader.
L949 | HISTORY-LOADBEARING | Until CB-134 the same declared contract meant four different things across two interpreters.
L949 | RULE | CB-78's `SIG_DFL` covers a pipe whose reader went away; it cannot cover a stdout that is already closed, because no write reaches the kernel and no signal is raised.
L949 | MEASURED | Measured on 3.13.3 and 3.14.4, one mutating verb, two spellings of "closed": `sys.stdout.close()` gives exit 1 + a raw traceback on both, but the write lands on 3.13 and not on 3.14.
L949 | MEASURED | 3.14's argparse touches stdout while the parser is being BUILT — `add_argument` → `_get_validation_formatter` → `_colorize.can_colorize` → `os.isatty(file.fileno())` — and `can_colorize` guards only `OSError` while a closed object raises `ValueError`.
L949 | MEASURED | `fd 1` closed at exec gives 120 with "Exception ignored on flushing sys.stdout" on 3.13, and 0 silently with the write landed on 3.14.
L949 | HISTORY-LOADBEARING | That last cell is the dangerous one and it is the newest: 3.14 sets `sys.stdout` to `None` for an invalid fd 1, `print` is a documented no-op against `None`, and the colour probe short-circuits on `hasattr(None, "fileno")` — so every verb runs, discards its whole output, and reports success.
L949 | RULE | That state is the "silent exit 0" CB-78's ratification rejected by name, reached by upgrading the interpreter rather than by changing any code here.
L949 | EXAMPLE | `codebugs export-csv /dev/stdout | gzip > backup.gz` reports success over a backup that was never written.
L949 | RULE | `cli.run` now REFUSES at the process entry, before any work, with the same 141 — one vocabulary for one condition ("the reader of my output is gone"), uniform on 3.11 through 3.14.
L949 | HISTORY-LOADBEARING | That range used to read "every interpreter `requires-python` admits", and CB-135's pin is what made the wider wording indefensible: every subprocess in `tests/test_cli_signals.py` is spawned with `sys.executable`, so the suite measures the one interpreter it runs under, and before the pin the range was covered only by different people happening to run different versions.
L949 | RULE | Pin that variable and nobody ever runs the others again — a claim about a range, held up by an accident that had just been removed.
L949 | IDENT | The `contracts` matrix in `.github/workflows/ci.yml` replaces the accident with a measurement (`test_cli_signals.py` + `test_fsio.py`, 38 tests, ~1.7s per version).
L949 | BOUNDARY | Honest scope: 3.15 and later are admitted by `requires-python` and are NOT verified until they are added to that matrix.
L949 | BOUNDARY | The alternative — narrowing the uniform-141 claim to the pinned version alone — was rejected as more expensive, since it would leave `requires-python = ">=3.11"` advertising a range nothing checks.
L949 | BOUNDARY | The price is a real behaviour change on 3.13, named rather than absorbed: a closed-object stdout there used to let the write land and then fail on output, and now lands nothing — which is the point, since the refusal precedes any committed write.
L949 | RULE | `sys.stdout = None` before `sys.exit` is INSURANCE, and a mutant deleting it SURVIVES.
L949 | HISTORY-LOADBEARING | Said that way round because the first draft of this sentence claimed the line was load-bearing and no test could discriminate it.
L949 | MEASURED | The mechanism is real and measured on both interpreters: with content already buffered on a bad descriptor, finalization's flush fails and rewrites the status 141 → 120, which is where the 3.13 fd-closed cell's 120 came from.
L949 | BOUNDARY | It is not reachable from the gate, because nothing has written to stdout by the time the gate refuses, so the buffer is empty and the flush succeeds.
L949 | IDENT | `test_premise_a_failed_shutdown_flush_rewrites_the_exit_status` pins the mechanism rather than pretending the gate exercises it.
L949 | RULE | The `sys.stdout = None` insurance line lives in `run` and could not live in `main`, for the same reason `signal.signal` could not.
L949 | RULE | The probe reads the descriptor's ACCESS MODE, and `fstat` was measured insufficient.
L949 | MEASURED | With fd 1 closed at exec, CPython's own startup opens a file onto fd 1 (the lowest free descriptor) — `/sys/kernel/mm/transparent_hugepage/enabled`, read-only — so `fstat` succeeds on a descriptor that raises `EBADF` on the first write.
L949 | RULE | A stream with NO descriptor is treated as USABLE (`StringIO`, a pytest capture object), deliberately the opposite of this repo's fail-closed default — here the conservative direction is to do the work, not to refuse it.
L949 | RULE | The predicate claims less than its name: `False` means proven unusable, `True` means not provably broken — never "a write will succeed" — the same affirmative-proof shape as `reconcile.live_source_clause`.
L949 | HISTORY-LOADBEARING | Cross-model review rejected the first draft of the predicate's claim for claiming more than "not provably broken".
L949 | BOUNDARY | Residual 1: `fileno()` does not govern `write()` — `io.TextIOBase()` raises `UnsupportedOperation` from both, so it is accepted and then fails, and refusing it instead would refuse every pytest capture object.
L949 | BOUNDARY | Residual 2: a writable descriptor can still fail to be written (`/dev/full`, a full filesystem, a hung-up PTY) — that is a write failure, not a closed stdout, and needs its own non-input-error outcome as a separate negotiation.
L949 | BOUNDARY | Residual 3: a file opened for WRITING landing on fd 1 passes the probe and takes the output.
L949 | BOUNDARY | Residual 4: the 141 is not unconditional — finalization also flushes `sys.stderr`, and a failing stderr flush rewrites the status to 120 even with `sys.stdout = None` (measured on both interpreters), reachable only by installing a broken stderr in-process before `run`, so no CLI invocation reaches it and making it unconditional would mean `os._exit`.

L950 | RULE | A CLI handler that writes a file uses `fsio.atomic_write`, never a bare `open(path, "w")` (CB-76).
L950 | HISTORY-LOADBEARING | `open(w)` truncates the destination before the first byte, so any write failure destroys the previous file.
L950 | MEASURED | Measured: a 34-byte export ends at 0 bytes on a simulated `ENOSPC`, and the `OSError` escaped as a raw traceback besides.
L950 | RULE | The obvious guard (`except OSError`) is a trap: it converts that traceback into one tidy line over a file that is now empty.
L950 | EXAMPLE | `import_markdown` (`reqs.py:564-566`) silently `continue`s past unmatched lines, so a truncated export round-trips as a successful, empty import.
L950 | RULE | The helper writes a temp beside the destination and `os.replace`s it only after the handle closed successfully.
L950 | WHY-NARROW | Quota and `ENOSPC` failures usually surface at flush/close, so replacing before that would install a bad file while reporting failure.
L950 | RULE | It refuses a read-only destination inside a writable directory, using `os.access` with effective ids.
L950 | WHY-NARROW | `open` authorizes on the file, `os.replace` on the directory, so without the check the fix would overwrite what the old code refused; effective ids are used because the default real-uid check would falsely refuse under setuid.
L950 | RULE | It writes in place, never replaces, when the destination is a FIFO/char device or an inode this process already holds open.
L950 | WHY-NARROW | That clause is what keeps `export-csv /dev/stdout > out.csv` working; a node-kind check cannot substitute for it because `realpath("/dev/stdout")` resolves to the redirect target, an ordinary regular file (measured).
L950 | RULE | It treats only `FileNotFoundError` as "missing", so a symlink cycle's `ELOOP` refuses instead of being classified as absent and replacing the link.
L950 | RULE | It resolves the path before taking `dirname` so the temp lands beside the resolved target (same filesystem, and the right directory for a symlinked destination).
L950 | RULE | `/dev/stdout` needs BOTH halves of the alias check.
L950 | HISTORY-LOADBEARING | That cost a review round: with stdout redirected to a file, `realpath` yields that regular file and only the held-open-inode test catches it; with stdout on a pipe, `realpath` yields `/proc/<pid>/fd/pipe:[N]`, which does not exist, so `os.stat` raises `FileNotFoundError` and a stat-based classifier reads "new file to create" and tries to `mkstemp` inside `/proc`.
L950 | RULE | Two resolutions of one path, neither check sufficient alone — the fd-directory test therefore runs before the stat.
L950 | HISTORY | An earlier draft gave a false reason for the ordering: `mkstemp(dir="")` does NOT fail, it creates in the cwd (measured).
L950 | RULE | Deliberate narrowing 1 of 3: a writable file inside a non-writable directory exported before and now fails cleanly, because atomicity is impossible there and an errno-keyed fallback cannot tell that case from `ENOSPC`/`EDQUOT`.
L950 | RULE | Deliberate narrowing 2 of 3: block devices are refused (a partial direct write corrupts persistent bytes).
L950 | RULE | Deliberate narrowing 3 of 3: a socket changes only its errno, since `open(sock,"w")` already fails today with `ENXIO`.
L950 | HISTORY-LOADBEARING | An earlier draft of this sentence counted two narrowings and lumped sockets in with block devices, which is this repo's own enumeration failure committed inside the bullet that cites it.
L950 | BOUNDARY | What replacement cannot carry — ownership, ACLs, xattrs, hard-link aliases, and `fsync` durability — reaches users through the CHANGELOG entry; the module docstring carries the same list for the next maintainer (a different audience, not a user-visible channel).
L950 | IDENT | `tests/test_fsio.py::TestWriteCallSitesRatchet` enforces this rule by AST rather than leaving it as prose.
L950 | HISTORY | The first draft of that ratchet grepped source text and matched `open(path, "w")` inside three of `fsio.py`'s own docstrings.

L951 | RULE | Where `init` creates is decided by the CHANNEL, not by the fact that a root was declared (CB-48).
L951 | HISTORY-LOADBEARING | This bullet used to read "`init` creates where you stand, and a declared root redirects only reads", which flattened two channels `db.declared_tracker_root()` already tells apart.
L951 | RULE | `$CODEBUGS_ROOT` is ambient — exported into a shell days ago, inherited by an unrelated subprocess — so it still redirects reads only; ambient state must never conjure a tracker in a directory the user is not in.
L951 | RULE | `--tracker-root DIR` is typed on the command line being run, so it is an assertion about this invocation, exactly as `project_dir`/`--repo` is, and `--tracker-root DIR init` therefore initializes DIR.
L951 | RULE | Precedence is one rule for reads and writes alike — argument > flag > env > walk — so a positional `init DIR` still outranks the flag.
L951 | RULE | Any surviving mismatch is announced on stderr, because otherwise `init` reports success for a tracker every other command will ignore — a success-shaped signal for a dead end, the same class of lie as CB-15/CB-16.
L951 | HISTORY-LOADBEARING | The defect this fixed was worse than the ignored flag itself: the warning fired on the path where the flag had been dropped, printing "commands will read DIR, not CWD" immediately after initializing CWD — two adjacent lines asserting the opposite of what was on disk.
L951 | IDENT | `TestInitUnderTheTrackerRootFlag` asserts the directory that must NOT have a tracker on every case (a test that only checked "the target got a tracker" could not see the defect).

## VERBATIM-CRITICAL

- `ValueError` — L915, L918
- `KeyError` — L915, L918
- `dict[str, Any]` — L921
- `cli.domain_errors()` — L918
- `with domain_errors():` — L918
- `json.JSONDecodeError` — L918, L919
- `sys.exit(1)` — L917, L918
- CB-159 — L918
- `tests/test_findings.py::TestDomainErrorsOrderingPin` — L918
- `TestRetriageCliContract::test_a_committed_write_is_never_reported_as_bad_input` — L918
- `_cmd_query` — L918
- `_cmd_reqs_query` — L918
- CB-19 — L918
- `_cmd_reqs_update` — L918
- T-57 — L918
- `7e46180` — L918
- CB-79 — L919
- CB-71 — L919
- `open(` — L919
- `os.getcwd()` — L919
- `subprocess` — L919
- `Path.read_text` — L919
- `sqlite3.connect` — L919
- `reqs-verify` — L919
- `FileNotFoundError` — L919, L950
- `PermissionError` — L919
- `provenance.file_status` — L919
- `provenance.py` — L919
- `db.git_rev_parse` — L919
- `OSError` — L919, L920, L950
- `subprocess.SubprocessError` — L919
- `CalledProcessError` — L919
- `TimeoutExpired` — L919
- `rename_output = ""` — L919
- `deleted` — L919
- `unknown` — L919
- `db._db_path` — L919
- `DatabaseNotFoundError` — L919
- `verify_requirements` — L919
- `_cmd_reqs_verify` — L919
- `db.row_to_dict` — L919
- `chmod 000 git` — L919
- `PATH` — L919
- `EACCES` — L919
- CB-99 — L920
- `reqs.import_markdown` — L920
- `except sqlite3.Error` — L920
- `SQLITE_FULL` — L920
- `{'imported': 0, 'skipped': 2}` — L920
- CB-86 — L920
- `sqlite3.IntegrityError` — L920
- `SQLITE_MISMATCH` — L920
- `SQLITE_TOOBIG` — L920
- `DataError` — L920
- `OperationalError` — L920
- `_is_environmental` — L920
- `INSERT OR REPLACE` — L920
- `len(cells) < 4` — L920
- `_ROW_RE` — L920
- `{'imported': 1, 'skipped': 1}` — L920
- `grep -rn "except sqlite3\." src/` — L920
- `tests/test_<module>.py` — L924
- `conn` fixture — L924
- `tmp_path` — L925
- `tests/conftest.py` — L926
- CB-204 — L926
- CB-215 — L926
- `CODEBUGS_ROOT` — L926
- `db.connect()` — L926
- `db._find_db_root` — L926
- `_resolve_db` — L926
- `cli.py:91` — L926
- `tmp_path_factory.getbasetemp()` — L926
- `--basetemp` — L926
- `TMPDIR` — L926
- `git rev-parse` — L926
- `.claude/plans/` — L926
- `tests/test_exposure_matrix.py` — L926
- `.claude/plans/exposure-scripts/matrix.py` — L926
- `.git` — L926
- CB-27, CB-30 — L928
- `mark_items` — L928
- `BEGIN IMMEDIATE` — L928
- `busy_timeout` — L928
- `tests/test_findings.py:504-547` — L928
- `b_started` — L928
- `b_read` — L928
- `conn.commit()` — L928
- `db.txn` — L928
- `conn.execute("COMMIT")` — L928
- `CommitPausingConnection` — L928
- `tests/test_milestones.py` — L928
- `uv run python -m pytest tests/ -v` — L929
- `uv run ruff check src/ tests/` — L930
- `uv run ruff format src/ tests/` — L931
- `register_tools(mcp, conn_factory)` — L934, L937
- `register_tool_provider()` — L934
- `server.py` — L935
- `_conn` — L935
- `str | list | None` — L936
- `register_tool_provider("name", register_tools)` — L937
- CB-28 — L938
- `blockers.deferred_ids_and_counts` — L938
- CB-69 — L938
- `docs/2026-04-04-blockers-design.md:278-291` — L938
- `get_deferred_item_ids` — L938
- `provenance.check_findings` — L938
- `done_commit` — L938
- `set_item_status` — L938
- `mark_integrated` — L938
- `meta_value` — L938
- CB-25 — L938
- `ids` — L938
- `TestDeferredEmptyIntersection` — L938
- `server.install_strict_arguments()` — L939
- `tools/call` — L939
- `extra="ignore"` — L939
- CB-15 — L939, L948 (referenced), L951
- `additionalProperties: false` — L939
- `MCPServer.middleware` — L939
- `tests/test_server.py` — L939
- CB-73 — L940
- `Tool.description` — L940
- `__doc__` — L940
- `server._NormalizedDescriptions` — L940
- `description=` — L940
- `MCPServer.tool()` — L940
- `_tool_manager._tools` — L940
- `dedent_docstring` — L940
- `tests/_mcp_schema` — L940
- `append_note` — L941
- CB-18 — L941
- `notes` — L941
- `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json` — L941
- `register_cli(sub, commands)` — L944, L946
- `register_cli_provider()` — L944
- `cli.py` — L945
- `--mode` — L945, L947
- `register_cli_provider("name", register_cli)` — L946
- `_register_builtins` — L947
- `init` — L947, L951
- `where` — L947
- `--tracker-root` — L947, L951
- `pre_parser` — L947
- `cli.run` — L948, L949
- `cli.main` — L948
- CB-78 — L948, L949
- SIGPIPE — L948
- `SIG_DFL` — L948
- 141 — L948, L949
- exit 1 — L948, L949
- exit 120 — L948, L949
- `BrokenPipeError` — L948
- "Exception ignored on flushing sys.stdout" — L948, L949
- `tests/test_fsio.py` — L948, L950
- `tests/test_findings.py` — L948
- `tests/manual/repro_cb76_truncation.py` — L948
- `pytest -q -s . | head -2` — L948
- `server.main` — L948
- `export-csv /dev/stdout` — L948, L949, L950
- `reqs-export` — L948
- `pipx reinstall` — L948
- CB-134 — L949
- `sys.stdout.close()` — L949
- 3.13.3 — L949
- 3.14.4 — L949
- `add_argument` — L949
- `_get_validation_formatter` — L949
- `_colorize.can_colorize` — L949
- `os.isatty(file.fileno())` — L949
- `ValueError` (argparse closed-object) — L949
- `sys.stdout = None` — L949
- `hasattr(None, "fileno")` — L949
- CB-135 — L949
- `tests/test_cli_signals.py` — L949
- `sys.executable` — L949
- `.github/workflows/ci.yml` — L949
- `contracts` matrix — L949
- 38 tests, ~1.7s per version — L949
- `requires-python = ">=3.11"` — L949
- `test_premise_a_failed_shutdown_flush_rewrites_the_exit_status` — L949
- `signal.signal` — L949
- `/sys/kernel/mm/transparent_hugepage/enabled` — L949
- `EBADF` — L949
- `StringIO` — L949
- `io.TextIOBase()` — L949
- `UnsupportedOperation` — L949
- `/dev/full` — L949
- `reconcile.live_source_clause` — L949
- `os._exit` — L949
- CB-76 — L950
- `fsio.atomic_write` — L950
- `open(path, "w")` — L950
- `ENOSPC` — L950
- `except OSError` — L950
- `import_markdown` (`reqs.py:564-566`) — L950
- `os.replace` — L950
- `os.access` — L950
- `realpath("/dev/stdout")` — L950
- `ELOOP` — L950
- `/proc/<pid>/fd/pipe:[N]` — L950
- `mkstemp(dir="")` — L950
- `EDQUOT` — L950
- `ENXIO` — L950
- `tests/test_fsio.py::TestWriteCallSitesRatchet` — L950
- CB-48 — L951
- `db.declared_tracker_root()` — L951
- `project_dir`/`--repo` — L951
- `TestInitUnderTheTrackerRootFlag` — L951
