# Bugfix loop ledger — codebugs

One row per iteration. Read this in Phase 0 so no card is re-picked and the report can state a
net change. Tracker: this repo's own `.codebugs/findings.db`, served by `mcp__codebugs__*`.

| Date | Focus | Cards | Disposition | Merge | Follow-ups |
|---|---|---|---|---|---|
| 2026-08-19 | planner-cascade **Т-1** (registry §3, brief BRIEF-cb58-claims-wiring) | CB-58 | **fixed — and three defects in my own work were found by three DIFFERENT mechanisms, which is the part worth keeping.** The harness "claimed" a card by flipping its status to `in_progress`: no holder, no exclusion, no release path, and `worktree-finish.sh` touched the tracker not at all. Now setup claims through `claims.py` BEFORE `git worktree add` (order is the point — claiming afterwards means the loser of a race already owns a branch and a directory when told no), handles exit codes as the API they are (3 fatal and names the incumbent, 4 warns, 5 sleeps and retries once), arms an EXIT trap that releases on abort, and finish releases guarded-and-never-fatal. The old status pre-check was COLLAPSED, not kept — two gates answering one question from different sources is this repo's shared-predicate lesson. **(1) The mutation probe caught a vacuous test of mine**: disabling finish's release with the shell no-op `:` left all 19 tests green, because the assertion was a substring search — a mention is not a call. **(2) The end-to-end read** (the artifact as one thing, not section by section) caught `mkdir -p .worktrees` still sitting above the claim gate, so a refused setup left a directory behind — and the gate's entire justification is that a refusal is free. **(3) Cross-model review surfaced this repo's OWN `FINAL-DESIGN.md`, which I had not consulted**: my trap disarmed at the END of the script, which is precisely the alternative §6.4 rejects ("releases ownership while a real worktree sits on disk"), and it was reachable because the verify step assigns from an unguarded command substitution. Codex itself timed out before emitting findings — what was useful was its trace showing which document it had opened, verified against the doc rather than its paraphrase | `9337175` (`2c656fc`, `a8e16ec`, `3c9e8c0`, `8211bde`, `fd1bf6f`) | **Two divergences from FINAL-DESIGN §6.2–§6.3 written into CLAUDE.md so nobody reverts them**: `--allow-duplicate` does not clear a `held_by_other` refusal (**ratified by the owner** — the flag is needed for ordinary follow-up work here, since merged branches are never deleted, so overloading it disables the gate exactly when people do normal work), and finish keeps restore ON (the design passes `--no-restore` because its `[7b/9]` auto-resolve already closed the card; codebugs has no such step, so `--no-restore` would leave every finished branch's card `in_progress` with no holder — this card's own defect, reintroduced by its fix). **Bootstrap**: the finish that LANDED this ran main's pre-merge copy, so the release block did not fire and the claim stayed live until the card was closed — same shape as CB-50/CB-57. Filed **CB-107** |
| 2026-08-19 | planner-cascade **Т-4** (administrative — registry §3) | CB-63 | **closed `wont_fix`, resolution APPLIED not re-litigated.** Iteration 10 recorded the owner's ratified MIXED answer to the field-freshness question — CB-52 (`severity`) took option (a), CB-53 (`reported_at_commit`) took option (b) — and two rationales for two columns is verbatim the condition CB-63 had **pre-registered under its own KNOWN WEAKNESS / residue clause** as the signal to split rather than stretch. So the container dissolved itself; this row only writes that fact into the card's status. **`wont_fix` and deliberately NOT `fixed`:** the card's stated exit is "contract ratified AND in CLAUDE.md's dedup section AND both members terminal", and CB-53 is still open (decision-free, implementation-only) — closing as `fixed` would assert an exit condition that has not occurred, which is this repo's primary defect class. Nothing is lost by closing: both members carry `meta.meta_bug = "CB-63"` and each carries its own ratified answer in full | — (tracker-only, no code) | CB-53 remains open and is now **decision-free** — implementation only. The linkage CB-63 existed to create already paid off (one question asked instead of two) |
| 2026-08-19 | planner-cascade **Т-4** (administrative — registry §3, Б-6 backlog) | — (projection rows, not cards) | **7 stale `stream/triage` projections reconciled `open → done`.** The registry asked me to reconcile a delta: ledger iteration 11 measured **7**, a later SELECT reported **6**. **The answer is 7** — CB-26/27/30/36/39/40/41, exactly iteration 11's list, every source `fixed`. Measured by `codebugs milestone-reconcile`'s own dry run, which is the sanctioned surface; the "6" did not reproduce. **The mechanism is working and was not touched** — this is pure backlog left over from rows written before the CB-26 status-change hook existed, which is why a purpose-built one-time repair tool (`reconcile_all`, dry-run by default) already exists and is what ran. Verified after: `(nothing to reconcile)` | — (tracker-only, no code) | **Process note against myself:** I first counted these with two read-only `sqlite3` probes. The user forbade direct SQL and required the codebugs MCP surface; both are now durable memory. The conclusion stood only because the tool's own dry run independently produced the same 7. **Gap found while complying: `milestone-reconcile` is CLI-only — there is no `milestone_reconcile` MCP tool**, so an MCP-only client cannot run this repair |
| 2026-08-19 | `codebugs` ("stabilization batch, batcheable items, related bugs or same files") | CB-69 (CB-84 dropped) | **fixed — and the batch was refused for the THIRD time in three iterations, but this one is different: the reviewers did not merely fail the predicate, they proved my planned fix was a REGRESSION.** CB-69+CB-84 looked like the one legitimate cluster in the queue (predicate 1: the blockers layer has no collection-level active-blocker evaluation) and was ratified as such by a cross-model shortlist pass the iteration before. Both attackers returned SPLIT independently, Codex at 0.97. **The measured refutation**: my plan routed CB-84 through CB-69's whole-type summary, trading O(items) for O(all blockers in the tracker) — a 5-item milestone in a 200-blocker tracker goes from **7 statements to 203** — and `_items_with_active_blockers` has a SECOND caller I never found, `closegate.py:234`, INSIDE `db.txn`. So I would have lengthened an exclusive-lock hold, which is verbatim the anti-pattern I had written into CLAUDE.md for CB-31 one iteration earlier. My own reproducer held blockers at 2 and varied only items — the single axis on which the change looks good. Predicate 1 also failed its own falsifiable test on the letter (CB-84 needs item-scoping, a swallow replacement and a type narrowing; CB-69 needs none), putting the edit table at five rows against a ceiling of four. **CB-69 alone is clean and exactly halved the cost**: `2*(1+B)` → `1+B` statements (6→3, 22→11, 62→31), and through the wrapper the linear coefficient went 2→1 with the constant unmoved. **The reviewed-away design is the keeper**: my first shape was a `counts=` cache parameter, and both models killed it because a wrong-scope summary returns an empty set with no error, which both wrappers turn into a ZERO-ROW PAGE — CB-25/CB-28's failure installed by a performance fix. The shipped API takes `entity_type` once and returns both halves, making the mismatch unrepresentable | `543ac64` (`f5ec585`, `13190c7`, `9bac0f3`) | **CB-84 restored to `open`**, unclaimed, nothing implemented, branch renamed `fix/cb-69-blocker-single-pass` so its ids equal the active cluster — and the card is materially better: re-scoped from ONE site to its real THREE (`_spine._items_with_active_blockers` with both callers, `capacity._has_active_blocker` inside `pull_next`'s `db.txn` which neither card nor plan had named, `blockers.get_unblocked_by`), with the pessimization table, the `closegate` caller, and the correction that its `items + 4` holds only for MANUAL triggers (`entity_resolved` gives `N+10`). Also settled there: the type-blind→type-aware narrowing is REACHABLE, not theoretical — `reqs.add_requirement(req_id="CB-1")` is accepted (measured), no entity writer validates id format. **Mutations 8/8, and three survivors in the first pass were all real test gaps**: nothing exercised the WRAPPERS (where the cost lands), every blocker in the differential fixture was ACTIVE so `is_active` was untested, and the reqs `blocker_count` annotation was pinned by NOTHING — deleting it left all 1628 tests green. **Process slip worth recording: I ran `git checkout -- src/codebugs/blockers.py` in an experiment and destroyed two uncommitted source edits**, then a `&&` chain let a commit land with a failing test. Both caught and amended; the skill warns about exactly that checkout in exactly those words |
| 2026-08-19 | `codebugs` ("stabilization batch, batcheable items, related bugs or same files") | CB-31 | **fixed — and for the second iteration running, the requested batch did not survive the clustering rule; this time the split was ratified by a cross-model pass over the SHORTLIST, before any card was picked.** The one legitimate cluster in the queue is CB-69 + CB-84 (predicate 1: the blockers layer has no single-pass active-blocker summary, so `deferred_id_restriction`, `blocker_counts_for` and `_spine._items_with_active_blockers` each re-derive it). It is not CB-31: that mechanism is the terminal-source status join, this one is blocker discovery — different tables, different predicates. Codex ranked the cluster third of four and called the union "theme-clustering wearing a mechanism costume". **REPRODUCED FIRST and the card's live number held**: `milestone_status("stream/triage")` reported `open_items: 44` against `triage_inbox`'s 37, with the 7 stale rows named (CB-26 CB-27 CB-30 CB-36 CB-39 CB-40 CB-41, all `fixed`) and 0 orphans — the same delta of 7 the card measured two days earlier at 30 vs 23. **The card's own open question got answered on the way past**: those 7 are not fresh drift through a bypass writer, they all closed around when CB-26's hook landed and nothing filed since 08-16 is stale, so the eager hook works and this is a backlog the read filter silently absorbs. **The sweep also found the population was THREE call sites, not the two the card names** — `foundation.list_milestone_items(live_only=True)` was added after filing and remembered the rule on its own, which is the card's own prediction coming true in the benign direction one iteration later. **The justification INVERTED under review and that is the transferable part**: revision 1 led with anti-drift, and both reviewers independently pointed out that an AST ratchet over the existing call sites buys the same "record the decision" property with zero new SQL — so the seam stands on the N+1 inside `pull_next`'s `BEGIN IMMEDIATE` window, which is what the card itself had said all along | `3537590` (`4bdafbe`, `c966c9c`, `5f66a2f`) | filed **CB-104** (the call-site ratchet pins the population that CANNOT have the defect — the hazard is a new read that omits the call, and an equality assertion over three existing names is blind to it; the complement ratchet needs a classifier, and the trap is that `closegate.py:212` filters status in Python so a SQL-text key misses it), **CB-105** (`list_milestone_items` re-exported but absent from `REEXPORTED_NAMES`, pre-existing, Codex-found), **CB-106** (`medium` — **found by USING the tools, not reading them**: an MCP client can open a `codemerge` session it can never close. `finish` refuses any status but `merging` in BOTH directions, `merging` is only reachable via `codemerge_merge` whose CAS legitimately refuses once the merge landed elsewhere, and the only exit — `abandon_session` — is CLI-only. Worse, `codemerge_finish`'s own MCP docstring promises `success=False` → `abandoned` while the code sets `active`. A stranded session's claims are reported as conflicts forever via `merge.py:441`; mine held four milestones files), evidence appended to **CB-37** (the WHERE-conditions accumulator, with the measurement that mis-splice is expressible at exactly one of the three sites and that site has mutation M8 covering it). **9/10 mutations killed**; M7 the one honest survivor (SQL-text determinism only, nothing can discriminate it). **Two review findings changed CODE, not prose**: an unqualified `alias` was measured failing **CLOSED** — hiding an `external` row that must stay live, because the correlated columns resolve against the source table first — and the layering fix then forced a second real change, since `NOT (item_kind = ? AND EXISTS(...))` is not equivalent to a single `NOT EXISTS` when `item_kind` is NULL (`WHERE NULL` excludes), needing SQLite's null-safe `IS` |
| 2026-08-19 | `codebugs` ("stabilization batch, batcheable items, if possible in one batch") | CB-52 (CB-63 dissolved, CB-53 unblocked) | **fixed — and the batch the user asked for was dissolved by the batch card's own rule, then the tree was narrowed a second time by a fact my first question had not carried.** CB-63 was a curated meta-batch pairing CB-52 (`severity` never escalates on a dedup bump) with CB-53 (`reported_at_commit` never refreshes, so staleness calls a just-re-observed defect stale) as ONE decision: which columns of a deduplicated row track the LATEST observation. The user ratified a MIXED answer — (a) for severity, (b) for provenance — which is exactly the condition CB-63 had pre-registered under its own residue clause as the signal to split. So the "batch" produced one tree, and CB-53 came out of it decision-free rather than merely deferred. **Then review moved the scope again, and this is the part worth keeping.** My scoping question had described the milestone-routing half as live harm; it is not. `milestone_status("stream/security")` reports `total_items: 0` — the `critical AND security:` predicate has never fired in this tracker's life — and **CB-35 already owns that question and explicitly recommends AGAINST the hook-based option my plan had proposed**. I had put neither fact in front of the user. Going back with the correction was the right call and it halved the tree. **The live defect was the third symptom, not the titular one**: `query(severity="critical")` returned `[]` for a card observed as critical seconds earlier — the tracker's primary read path could not see it | `c1220df` (`18e280e`, `16352e2`) | filed **CB-103** (population card: the field-freshness question covers FIVE columns, not CB-63's two — `source`, `tags`, `reported_at_ref` are unowned, and `tags` is the one with real cost since `query(tag=)` reads the column, not the ring). Evidence appended to **CB-35** (four design defects found before any code, plus "option (a) is now half-built"). **6/6 mutations killed — including one caught being VACUOUS inside the harness built to prevent vacuous kills**: a duplicate keyword argument PARSES cleanly and fails at `compile` time, so `ast.parse` passed it and pytest's import error was being scored as a kill. Gate moved to `compile()` |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-73 | **fixed — and found only because I re-read the queue instead of my own summary of it.** The previous row concluded the pure-bugfix seam was thin; that was true of the `low` queue, which is the only one I had been scanning. CB-73 sat in `medium`. The SDK reads `Tool.description` from `__doc__`, CPython 3.13 dedents docstrings at compile time and 3.11/3.12 do not, so on two of the three hosts `requires-python` promises, clients received the source indentation — and since MCP clients render descriptions as Markdown, CommonMark turned a 4-space-indented line after a blank line into an **indented code block**, rendering most tools' entire prose body as monospaced code. **Measured on both interpreters: 64/68 descriptions differed and 61/68 carried the code-block pattern; both 0 after, and 3.13 output byte-identical before and after** — which is exactly why the wire golden did not move. Normalized once at registration through `description=`, a PUBLIC parameter of `MCPServer.tool()`. **Two alternatives rejected and worth keeping**: rewriting `fn.__doc__` is a global side effect on another module's objects, and rewriting the registered `Tool` objects afterwards needs the SDK's PRIVATE `_tool_manager._tools` — I probed it, it *would* have worked, and that is the trap, since it is a worse coupling than the provisional-but-public seam `install_strict_arguments` already documents | `9af0545` | none filed. `dedent_docstring` moved out of `tests/` into `server.py` with the helper importing it — a test-side copy was harmless while it normalized only the *comparison*, but once the server emits normalized text a second definition is one drift from the gate and the server disagreeing. 4/4 mutations killed **including unwiring the registrar from `main()`**, and each verified to parse, to have landed, and to run under the worktree interpreter — the three checks whose absence produced three bogus kills last iteration |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-85 | **fixed, and reproduced first** — the card was filed last iteration as "code-read confirmed, reproducer designed but not run", and this repo's rule is that a card's leads get verified before they get carried. Measured on both trees with the same still-present, unchanged file: `chmod 000` on its parent directory gives `deleted` on main and `unknown/stat_error` here. `os.path.isfile` returns False for ANY stat failure — unreadable parent, ELOOP, stale NFS handle — not only for "absent", and that False skipped the `modified` branch and let the code below state `deleted` as a fact about a file it had failed to look at. **Second route to the answer CB-79 closed one line below** (that one swallowed a git failure into an empty rename result); same "guard reporting clean because it could not look" shape, different mechanism, which is why the split held. `FileNotFoundError`/`NotADirectoryError` deliberately keep today's path — both mean the target genuinely cannot exist, so `deleted` is right and the fix must not over-refuse | `5b45eb1` | none filed. **Review scope stated rather than skipped**: no cross-model round — single file, ~12 lines, no new API, no gate, and the fix shape was already specified on the card by the Codex review that found it. **THREE BOGUS MUTATION KILLS caught and redone, the most transferable thing here**: one mutation left invalid Python (killed by SyntaxError), two ran under a bare `python3` with no pytest (killed by ImportError), and one run aborted before its restore and left the mutation STRANDED in the worktree — found by checking `git status`, not by trusting the script. A mutation must parse, must be verified to have landed, must run under the worktree's own interpreter, and must restore in a `finally`. Final: 3/3 genuine kills |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-82 | **fixed** — `import_csv` resolved two arguments with truthiness (`date or utc_now()[:10]`, `run_id or _next_run_id(conn)`), so a falsey WRONG TYPE was indistinguishable from "not supplied" and the row landed under a date and id the caller never chose: `date=[]` stored today's date and reported success. **The card was wrong about two of its own examples** — it claimed `date={}`/`run_id={}` "reach the INSERT and raise", but `{}` is falsey and took the same silent path; only TRUTHY wrong types reached the INSERT. All five non-payload arguments are validated up front, before any parse or write. **The rule: on a write path `None` is the ONLY "not supplied"** — deliberately unlike the query side, where `""` also means "no filter", because an absent filter matches everything while an absent stored value must be *invented*. Scope verified rather than assumed: `import_json` forwards all four shared args here and there is exactly ONE writer into `codebench_runs`. **Codex returned FAIL_REVISE on the sharpest finding of the iteration**: my guard serialized `tags`/`meta` TWICE — once to validate, once at the INSERT — which (a) smuggled in an unrequested `allow_nan=False` narrowing that would have refused `meta={"x": nan}` that stores fine today, and (b) left a window where a mutable or `__iter__`-overriding subclass shows different data to each call — **CB-74's "validating one view while consuming another" in a new place**. Now serialized once, and that exact string is stored | `cf32977` | none filed. **4/6 mutations killed, and M1/M2 survive BY CONSTRUCTION rather than by test weakness** — with the guard in place those lines are unreachable for bad input, so `x or default` and `x is None` are provably equivalent there; the `is None` spelling is defence-in-depth no test can discriminate, said plainly rather than dressed up as covered. Behaviour change: `date=""`/`run_id=""` refused; no caller passes them. Out of scope and named: `date` FORMAT still unvalidated |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-79 | **fixed, and the card's own thesis turned on the card itself.** CB-71 swept for `open(` and closed five sites; that spelling structurally cannot see `os.getcwd()`, `subprocess`, `Path.read_text` or `sqlite3.connect`. Two holes reproduced by running them: `reqs-verify` from a **deleted cwd** printed a raw `FileNotFoundError` (a long-lived MCP server outlives the worktree it started in), and a **non-executable git** raised `PermissionError` out of `provenance.file_status`, whose guard caught only `FileNotFoundError` — *git is missing* and nothing else. **The card named ONE narrow tuple; sweeping for the SHAPE found FIVE**, including `db.git_rev_parse` which the card never mentions. All now catch `OSError` — a strict widening, with `subprocess.SubprocessError` kept because it is *not* an `OSError` subclass. **The widening then exposed a latent wrong answer** (Codex): `file_status`'s rename lookup swallowed its failure into `rename_output = ""` and the fall-through reported a confident **`deleted`** — the "guard reporting clean because it could not look" shape, one line below the guard being widened; now `unknown/git_error`. Two more Codex findings taken: the requirements root resolves **lazily** (it is consumed only by the `tests` check, so an eager `os.getcwd()` broke `checks=["ids"]` for a check that needs no directory), and `_cmd_reqs_verify` gains the `JSONDecodeError`-before-`ValueError` ordering plus the `finally: conn.close()` it never had. **Degrade-or-raise is decided by the CALLER's contract, not by the failure** — provenance degrades because that is already what it does when git is unreachable; `verify_requirements` raises because it has no `unknown` vocabulary and a false clean is worse | `8ba8c2a` | filed **CB-85** (`os.path.isfile` swallows stat errors into the same false-`deleted` route — a second, independent path to the answer this card just fixed) and **CB-86** (create-mode `sqlite3.connect`/WAL raises a non-contention `OperationalError`; NOT an `OSError`, so a third vocabulary of "the CLI crashed at an I/O boundary" that neither sweep could reach). 6/6 mutations killed, each verified to have LANDED — M1 matched twice and silently did not, the vacuous row that check exists for. **Process note: relative-path shell commands ran against MAIN after a cwd reset** — the trap CLAUDE.md documents at the *finish* step, hit at the *editing* step; an appended test block landed in main and was extracted and moved. Nothing lost; the source edits were safe because they used absolute paths |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-76, then CB-83 | **both fixed — and the headline is that a "simple" card needed THREE review rounds, each finding a NEW live regression inside my own fix.** Both export handlers wrote through a bare `open(path,"w")`: an unwritable path printed a raw traceback, and `open(w)` truncates BEFORE the first byte, so a write failure destroyed the previous export (measured: 34 bytes → 0 on a simulated `ENOSPC`). The card's own point is that `except OSError` alone is a TRAP — it turns that traceback into one tidy line over a file that is now empty, and `import_markdown` accepts a truncated file as valid, so it round-trips as a successful empty import. Fix is `fsio.atomic_write`: temp beside the resolved target, `os.replace` only after the handle CLOSED (quota/ENOSPC surface at flush/close). **Round 1** killed my fallback — the Opus adversary REPRODUCED DATA LOSS on it (66→16 bytes); an errno-keyed fallback cannot tell "directory not writable" from ENOSPC/EDQUOT, the very conditions the card is about. **Round 2** killed the `S_ISREG` dispatch: I measured `realpath("/dev/stdout")` returning a REGULAR file, so the guard never fired; Codex added the `os.access` real-vs-effective-uid false refusal and ELOOP-read-as-missing. **Round 3 (the simplify altitude pass)** found the branch had BROKEN `export-csv /dev/stdout \| cat` — on a PIPE `realpath` yields `/proc/<pid>/fd/pipe:[N]`, which doesn't exist, so the classifier read "new file" and tried to mkstemp inside `/proc`. The two `/dev/stdout` resolutions need two different checks and neither catches both | `3cc7d2c` (5 commits) + `e9443ba` | **CB-83 filed AND fixed in-iteration**: this tree's own integration committed a stray `tmpy_efkp4t` to main, because the harness guard for that class matched only `*.py`. Evidence appended to CB-55 (the stderr arm went 3 → 5 copies, and CB-76 built the natural host — `atomic_write` — then declined to use it). **Scope was the USER's call**: when correct and shippable diverged I asked, and "ship the full atomic design" authorized three deliberate narrowings. Batching CB-79 refused with the predicate named; both reviewers concurred |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-75 | **fixed, and the fix's own first implementation was bypassable** — `import_csv` fed `csv_data` straight to `io.StringIO`, so `csv_data=5` leaked `TypeError: initial_value must be str or None, not int` instead of the contracted `ValueError`. Guard is positive and up-front, never a rewrap (a rewrap would also convert a post-commit failure into bad input, CB-15/CB-16). **A Codex diff review then showed `isinstance(csv_data, str)` is spoofable** — CPython honours a `__class__` property, and `MagicMock(spec=str)` is such an object, so the leak survived its own fix; now `issubclass(type(...), str)`, which accepts exactly what the consumer accepts. **The general rule, CB-74's lesson in a second form: the guard's predicate must be IDENTICAL to the consumer's requirement.** Two deliberate asymmetries with `import_json`, both measured: bytes are refused rather than decoded (StringIO never took them, so nothing imports that way today), and no snapshot is taken because a `str` cannot present two views — verified with a lying `__str__` | `1e033d5` (`01abf7b`, `17b7423`, `cefde2a`) | filed CB-80 (population card: batch entry points; a str payload silently iterates CHARACTERS), CB-81 (`medium` — duplicate labels/headers/`nan` raise `IntegrityError` MID-WRITE, CLI-reachable traceback), CB-82 (the other five arguments unvalidated; falsey ones silently defaulted — CB-25's shape on a write path). **Three false claims of mine corrected**, incl. one inherited by the landed CB-72 class |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-71 | **fixed, and the card's own prescribed fix was rejected by measurement** — three CLI handlers performed a file read that no arm covered, so `bench-import missing.csv -b Q` printed a raw traceback. `_cmd_bench_import` *had* a `try` whose only arm was `(ValueError, JSONDecodeError)`; `_cmd_reqs_import` had **no arm at all** and leaked its connection. The card asked for a handler-wide `except OSError`; that would have reported a **landed** import as bad input, because the success `print` runs after the commit and raises `BrokenPipeError` (an `OSError`) on a closed pipe — reproduced with the run visible in `bench-list` afterwards. Guard covers **exactly the read**; `_cmd_import_csv`'s `open` is hoisted out of its `with`, which owned the whole import loop. **The tree also fixed a LIVE instance of the hazard the card only theorised about**: the pre-existing arm spanned the success print, so a post-commit `ValueError` from a closed stdout came back as the single line `I/O operation on closed file.` at exit 1 with the run committed. The card had listed hoisting that print as a *considered-and-rejected alternative*; it is the fix | `82ef895` (`f86c286`, `19d5e95`) | filed CB-76 (exports + truncation), CB-77 (mid-loop read semantics), CB-78 (post-success output failure, POSIX decision at `cli.main`), CB-79 (non-file `OSError` sources); evidence appended to CB-55. **Scope cut from 5 handler edits to 3 by review** — the two export handlers went to CB-76 |
| 2026-08-17 | `codebugs` (pure/simple/confident, "batch related") | CB-72 + CB-74 | **both fixed, one mechanism** — `import_json` checked that its argument was a non-empty list and nothing else, so two inputs walked past the module contract and out as stdlib exceptions: a payload outside `str\|bytes\|bytearray\|list` as `TypeError` (CB-72, in-process only), and an array whose *elements* are not objects as `AttributeError` from `data[0].keys()` (CB-74, **MCP-reachable** — wire type is `str \| list \| None`, and the SDK also pre-parses a wire string `"[1,2]"` into a list). CB-74 was filed by me during the sibling sweep and is the more serious half. Guard is a **positive shape check before `data[0]`, never an exception rewrap** — a blanket rewrap would also convert a post-commit failure inside `import_csv` into `ValueError`, which the CLI arm reports as bad input for a write that landed (CB-15/CB-16). **Every** element checked: `[{a,b}, 5]` never reached `data[0]` at all, it died later in `csv.DictWriter`. `bytes`/`bytearray` deliberately kept working (annotation widened) — refusing what imports today would be a behaviour change wearing a bugfix costume | `6bfde7a` (`bae08df`, `43f4d4b`) | filed CB-75 (`import_csv`'s `TypeError` twin — different accepted-type condition in a different function, so its own tree). **CB-71 returned to `open`** with full evidence: both reviewers ruled the 3-card cluster illegitimate. Next candidate is CB-71, and its card now carries the design work |
| 2026-08-17 | `codebugs` (pure/simple/confident) | CB-70 | **fixed** — the MCP wire-schema gate could not pass on 3.11/3.12, which `requires-python` promises: the golden was built on 3.13, which dedents docstrings at compile time, so 64 of 68 tools read as "drifted" and the message told the reader to regenerate — which would have broken it the other way. Replicated 3.13's own dedent instead of `inspect.cleandoc` (measured: cleandoc rewrites 61/68 entries and permanently blinds the gate to boundary whitespace), so **the golden was not modified at all**. Also collapsed the duplicated dump logic into one shared collector and pinned the golden as a fixed point | `59885ea` (`6d4ccc9`) | filed CB-73 (a 3.11/3.12-hosted server sends indented descriptions, which CommonMark renders as a code block for ~61 tools) — filed low, then **corrected to medium** when review showed my "cosmetic" framing was wrong. Both reviewers rejected revision 1 and converged on the same fix from different methods |
| 2026-08-17 | `codebugs` (pure/simple/confident) | CB-67 | **fixed** — bench's "exactly one of two" contract lived only in the MCP wrappers, so both CLI handlers picked a winner where it says refuse (`bench-import a.csv --json-file b.json` imported b.json and discarded a.csv at exit 0; `bench-delete --run-id R --benchmark B` deleted R and ignored B), and `codebench_import` validated with `is not None` while dispatching with `if csv_data:`, leaking `TypeError` on `csv_data=""`. One structural helper, four call sites, each keeping its own notion of "supplied" | `1778db2` (`d4df261`) | filed CB-71 (uncaught `OSError` on an unreadable import path) and CB-72 (`import_json` `TypeError` leak) — both pre-existing neighbours, verified by running them, split off per the clustering rules. **Codex FAILED revision 1**: unifying "supplied" as `is not None` everywhere would have made five undeclared behavior changes and turned `bench-import ""` into a traceback — the redesign unifies the XOR *structure* only |
| 2026-08-17 | `codebugs` (pure/simple/confident) | CB-64 | **fixed** — `claims --format table` (the default) crashed on any live claim: `claims.py:781` passed `(columns, rows)` swapped into `format_table(rows, columns)` with list rows; empty path printed blank lines instead of `(no results)`. Both symptoms reproduced, TDD tests proven failing first; sibling sweep over all `format_table` sites: only instance. Batching refused (no clustering predicate holds among CB-64/67/68; dry-run report concurs) | `3317c6f` (`ebc886b`) | none filed. Codex re-ranked the tail: CB-67 (bench falsey-dispatch + CLI cross-arg, pure bugfix) next, then CB-68 (convention, arguably not a pure bug). A general `test_cli.py` for the untested CLI-handler layer stays on CB-64's structural note / CB-55 |
| 2026-08-16 | `CB-50` (process, user-named) | CB-50 | **fixed** — the workflow written into CLAUDE.md at 13:37 was violated at 15:30 by a fast-forward merge from an untyped branch, and the section had explicitly declined the harness that would have bound it. Ported autosorter's `tools/worktree-*.sh` scaled to this repo, plus a git pre-commit hook and `merge.ff=false`. Two cross-model reviews found **13 real defects**, one of them CRITICAL and introduced by round 1's own fix | `238d125` (5 commits) | filed CB-57 (merge of an untyped branch still uncaught — the surviving half of the incident), CB-58 (setup's "claim" bypasses `claims.py`, no release path), CB-59 (**no branch protection, no CI — both reviewers argued this is the real enforcement**). Every landing after this one goes through the harness |
| 2026-08-16 | `CB-45` (handoff-directed) | CB-45 | **fixed** — similarity extension (trigram-Jaccard file-time `similar_to` annotation + auditable `similarity-report`) and the pre-add resolver seam built with its first consumer; the card's 0.95 threshold measured FALSE and shipped as calibrated 0.7 under the letter-fix protocol | `566f547` (`02faaaf`) | CB-46 formally unblocked (its dry-run tool now exists; merge policy still PROPOSED, not ratified — plan D6); reqs-identity decided NO; **pipx server reinstall needed**; autosorter auto-filers still don't pass `fingerprint=` (pre-existing, not this card) |
| 2026-08-16 | `CB-49` (user-named) | CB-49 | **fixed** — relative declared tracker roots leaked verbatim into `where`, the MCP preflight, and the fail-closed error texts; now absolutized (lexical `abspath`, never `realpath`) at the single read point `declared_tracker_root()`, with a raw-value fallback so `describe_root` still never raises on a deleted cwd | `994ebb7` (`67a5ccd`) | none filed — sweep found the same shape in `_resolve_db`'s `project_dir` branch, but its only reader typed the path in that same cwd, so no harm path. CB-45 branch also touches `db.py`/`cli.py` (different hunks); its merge should rebase-check |
| 2026-08-16 | `CB-44, then CB-43` (user-named) | CB-43 + CB-44 | **CB-43 fixed** — findings identity function (fingerprint upsert, occurrence ring, regression reopen + milestone reopen projection); **CB-44 closed as a ratified decision** (identity is core, no seam — the card's option (b)) | `1e06a80` | filed CB-45 (similarity extension + seam design), CB-46 (backfill, blocker-linked to CB-45). **MCP server restart needed** before the new add semantics are live |
| 2026-08-13 | `codebugs` | CB-16 | **fixed** — meta clobber in `update_finding` / `update_requirement` | `1d85756`, hardening `63d0658` | CB-18 unblocked |
| 2026-08-13 | `codebugs` | CB-4, CB-1, CB-5 | **closed stale** with evidence — all three describe code that has since been refactored | `6e5236c`, `63d0658` (doc corrections) | sweep the remaining arch-debt cards |
| 2026-08-13 | `codebugs` | CB-18, CB-15 | **fixed** — `append_note` unreachable from either surface; unknown argument names silently dropped | `6a1aef2` (`987fc20`, `d6ce8de`) | CB-17 left open by design |
| 2026-08-13 | `codebugs` | CB-17 | **fixed** — severity was write-once, so a re-measured card could not be re-triaged | `dc49160` (`741f428`) | filed CB-19, CB-20; upgraded CB-6 |
| 2026-08-13 | `codebugs` | CB-17 (post-hoc) | **simplify pass** that should have run before landing — 3 redundant tests, a duplicated fixture, and two false doc claims | `1892b80` | filed CB-21 |
| 2026-08-13 | `codebugs` | CB-20 | **fixed** — vocabulary columns ordered alphabetically, so `low` outranked `medium` and `could` outranked `must` | `f9a682e` (`2ac27c4`) | filed CB-22 |
| 2026-08-13 | `codebugs` | CB-22 | **fixed** — an allowlist that never validated its own members; sibling in `capacity.py` silently lost an increment | `e1900d4` (`4db5a07`, `6996b8e`, `d1fea09`) | CB-21 still needs a user decision |
| 2026-08-13 | `codebugs` | CB-19 | **fixed** — severity had no resolver; the sweep found query filters comparing raw text against canonical columns in both entities | `071a630` (`f1f4bd0`, `d1a31f5`, `3f91704`) | queue now blocked: every remaining card needs a product decision |
| 2026-08-13 | `codebugs` | CB-24 | **fixed** — meta merged in Python over a row read in a separate statement, so concurrent writers erased each other silently | `c3491c8` (`2495998`, `2d70e06`, `6d42fdb`, `3901425`, `f296852`) | filed CB-27; **CB-23 needs a user decision and is the highest-severity card left** |
| 2026-08-13 | `codebugs` | CB-23 | **fixed** — a named or declared root accepted a `.codebugs/` directory with no database and created one, silently | `6834775` (`e8f0ece`, `222152c`, `e803f78`) | queue: CB-21, CB-25, CB-26, CB-6, CB-27 — CB-25 is the only one needing no decision |
| 2026-08-13 | `codebugs` | CB-25 | **fixed** — vocabulary filters guarded by truthiness, so a falsey non-string returned the whole table; sweep grew 3 named sites to 9 | `c55f290` (`fd77d00`, `9b9ea2e`, `aac4904`) | filed CB-28, CB-29 — **every remaining card now needs a product decision, and CB-6's CLI-parity policy gates two of them** |
| 2026-08-13 | `codebugs` | CB-28 | **fixed** — the `deferred` pseudo-status discarded every other filter; forwarded per the April design doc instead of refusing | `a29fd50` | filed none; **CB-6 still the keystone, unanswered** |
| 2026-08-14 | `codebugs` | CB-26 | **fixed** — a derived queue trusted to a write-time hook alone; 19 of 23 open triage rows pointed at terminal findings. Backfill RUN, not just shipped | `004027e` (`46a96ec`, `ccd4fbb`) | filed CB-30–CB-35; row added retroactively 2026-08-14, the iteration left the table unwritten and its section marked IN FLIGHT after landing |
| 2026-08-14 | `codebugs` | CB-40 + CB-41 + CB-39 + CB-36 batch 6 | **fixed (4 cards)** — both raw `BEGIN IMMEDIATE` sites absorbed into `db.txn`; the merge lock's expired-lease double-admission closed; **CB-36 complete, all 13 sites**. Merged under a standing NO-GO by explicit user decision, residual filed | `19e4947` | filed CB-41 then CB-42 (irreducible TTL window + missing fencing token). **FOUR Codex rounds; three of the defects were in code written to fix the previous one** |
| 2026-08-14 | `codebugs` | CB-36 batch 5 of N | **partial, card stays `in_progress`** — `closegate.milestone_close`, the only cross-table site; 12 of 13 done. **The last site (`merge.merge`) is decision-blocked on CB-40, not effort-blocked** | `162f19f` | none filed; sequence is decide CB-40 → fix `merge.merge` + CB-40 in one tree → close CB-36 |
| 2026-08-14 | `codebugs` | CB-36 batch 4 of N | **partial, card stays `in_progress`** — `triage.triage_dismiss`, held back from batch 3 because its propagation nests inside `update_finding`'s own `db.txn`; 11 of 13 done | `4ee26b1` | none filed; last 2 (`milestone_close` cross-table, `merge.merge` + CB-40) each need a bespoke fix |
| 2026-08-14 | `codebugs` | CB-36 batch 3 of N | **partial, card stays `in_progress`** — four milestone item writers (`move_milestone_item`, `set_item_status`, `milestone_defer`, `mark_integrated`); 10 of 13 done, and all four also lost CB-39's post-commit re-read | `33eb1bd` | none filed; the last 3 each need a non-mechanical fix |
| 2026-08-14 | `codebugs` | CB-36 batch 2 of N | **partial, card stays `in_progress`** — `blockers.resolve_blocker`, `sweep.add_items`, `sweep.archive_items`; 6 of 13 sites done | `77de4b2` | none filed; batch 3 = the four clean `milestones/` sites |
| 2026-08-14 | `codebugs` | CB-36 batch 1 of N | **partial, card stays `in_progress`** — `merge.py` session lifecycle (`start_session`, `finish`, `add_claim`); 3 of 13 sites done | `80e8ccf` (`6dc89d1`, `626fa2b`, `b88a646`) | filed CB-40 (both raw `BEGIN IMMEDIATE` sites commit an ambient caller transaction); two test gaps handed forward on the card with recipes |
| 2026-08-14 | `codebugs` | CB-27 + CB-30 (both re-scoped) | **fixed** — CB-24 conformance for the two live unwrapped read-modify-write sites; the sweep found the defect is package-wide (19 instances, 13 still open) | `ae77cba` (`89ae282`, `8a870bf`, `12af24f`, `0a2b3e5`, `4530006`) | filed CB-36 (`high`, the 13 remaining sites), CB-37 (enforcement, carried from CB-27), CB-38 (capacity policy, carried from CB-30, reframed), CB-39 (`pull_next`, same window) |

## 2026-08-17 — CB-72 + CB-74 (the guard that reintroduced the bug it was fixing)

**The batch the user asked for was refused, by both reviewers, on my own admission.** The tree
started as CB-71 + CB-72 + CB-74 and the plan openly conceded that no clustering predicate covered
CB-71 — it was kept on "the ceilings are clear". `bug-clustering.md:8` says one tree *iff* a
predicate passes **and** the ceilings pass; ceilings are necessary, not sufficient. An Opus adversary
and Codex reached that independently and phrased it the same way. **Writing down that I was
stretching a rule did not make the stretch legitimate; it just made it reviewable — which is the
point, but only because I then took the answer.**

The split turned out to be load-bearing rather than procedural. Review found CB-71's fix leaves the
card's own symptom half-open: wrapping the file read does nothing about the `BrokenPipeError` on the
**success** `print()`, so `bench-import good.csv -b P | true` still emits a traceback. That is an
unresolved design question, and the hostage test exists exactly so two finished guards do not wait
on one.

**The guard reintroduced CB-74 inside its own fix, and the whole test class was green.** The first
implementation *iterated* to validate and the code after it *indexed* (`data[0]`) and iterated again.
A `list` subclass whose `__iter__` disagrees with `__getitem__` therefore showed mappings to the
check and a non-mapping to the consumer — CB-74's exact `AttributeError`. Found by a Codex **diff**
review, not by either plan review, and not by 1275 passing tests. New spelling of an old lesson:
**validating one view while consuming another is not a guard**, the sibling of "sharing an
implementation does not share a decision if the callers supply different inputs".

**Three test lessons, two of them mine.**
- A **regression test can assert the wrong outcome for the right defect.** My first `SplitList` test
  demanded a `ValueError`; with the snapshot in place that payload is coherent and imports the row
  it validated, so the test failed on the *fixed* tree and looked like a broken fix. The
  discriminator was "no `AttributeError`" all along. Caught by running it.
- I shipped a **cannot-fail test and labelled it a premise pin**: `not issubclass(TypeError,
  ValueError)` is a property of Python, not of this code. Codex called it a tautology; it was
  deleted. The real non-vacuity evidence is the recorded run against main, which belongs in the
  docstring.
- **Fragment regexes certify wrong messages.** `"element 0 .*not int"` also matches a refusal that
  names the accepted type set incorrectly. Anchored whole with `re.escape`.

**A self-inflicted process error worth the line:** after committing the fix I made further
uncommitted edits and then ran `git checkout <sha> -- src/...` to mutate for a non-vacuity check —
destroying the uncommitted work. The skill warns about this in exactly these words. `git stash
push/pop` for the second attempt.

**Net change: 2 closed (CB-72, CB-74), 2 filed (CB-74 — filed and closed in this iteration — and
CB-75).** Open went 31 → 32, and the +1 is **not** mine: CB-59 returned `in_progress → open` when
another session landed its CI-loop fix, since branch protection is repo configuration that cannot be
enabled from inside the repo. My own contribution to the open count is zero. The queue is filling
with verified neighbours found by sweeps, not noise.

## 2026-08-16 — CB-45 (the similarity layer, and the seam built with its first consumer)

Handoff-directed pick (the CB-43/CB-44 handoff names CB-45 as the next unit). Plan with the full
review appendix: `.claude/plans/CB-45-similarity-seam.md`. Merge `566f547`; 1098 tests on merged
main (which had gained CB-48 and CB-49 in parallel while this iteration ran).

**Measured before designed, and the measurement overturned the card.** The card's ratified target —
"a 0.95 similarity ratio collapses the 115-row family" — was measured FALSE on the real corpus
(3162 rows at calibration, 3168 by landing): at 0.95 only 77 rows collapse and the family never
unifies, because those 115 rows are ~10 genuinely distinct defects (different failure tails: WAL
checkpoint vs get_ack timeout vs sqlalchemy pool). Threshold 0.7 groups 111/115 into coherent
subfamilies with zero observed false merges, corpus-wide collapse 102 rows vs exact identity's 71.
Shipped 0.7 under the letter-fix protocol (one-line notification delivered in-session); the "one
115-row family" target is dropped as exactly the false merge CB-43's RISK section forbids.

**adversarial-review-x2 on the plan: 6.5/10, 14 mandatory fixes, every one encoded before code.**
Twelve findings corroborated by BOTH models (highest-confidence set: runner-commits-outside-
transaction — SAVEPOINT/RELEASE at top level IS a commit, verified empirically by three parties;
the findings-table reach-in; import-order-dependent reserved keys; unrepairable annotations).
Codex-only catches: the plan's CSV justification was factually false (`_cmd_import_csv` passes no
`finding_id`), and a resolver can destroy the caller's transaction through the raw connection —
the review's sharpest finding. Opus-only: the report grouped terminal rows into a merge dry run;
cleanup masking the real error. The defender's fresh measurements defused two attacks (calibration
reproduces identically through the shipped normalization; the 43-row family is ONE defect — all 43
share the WAL-checkpoint tail, so Codex's false-merge accusation died on inspection while its
structural chaining point survived). Cross-model pattern: Codex stronger on hostile-input/data-flow,
Opus on repo-rule archaeology. Neither alone produces the full list.

**What shipped.** (1) `db.register_pre_add_resolver` — the seam CB-44 refused to build
speculatively, now built against its first consumer with the never-commit contract ENFORCED
(entry guard, post-resolver corruption check outside the swallow, guarded cleanup, outcome
validation inside the savepoint). (2) `findings.similarity_candidates` — the sanctioned accessor
that keeps similarity.py at ZERO SQL (no module reads another's tables; the review caught the plan
about to become the first violator). (3) `similarity.py` — trigram-Jaccard detector over the
identity normalization + ANSI strip, file-time `meta.similar_to` annotation (pool = live ∪
dismissed with status stamped; `fixed` excluded), offline `similarity-report` with per-family
DIAMETER (`min_pair_score` over all pairs — the corpus's 43-family hides a 0.392 pair behind
0.7+ edges, and an edge-minimum can never show it), edges, and description excerpts — CB-46's
sample-auditable dry run. (4) Requirements-parity DECIDED: no identity function for reqs (the card
delegated it; caller-assigned ids on every write path, zero automated filers, embeddings covers
similarity). (5) `tests/manual/verify_similarity_corpus.py` — the calibration is a committed,
reproducible artifact; it reproduces 11 families / 102 collapse exactly, and after the diff review
it ENFORCES that tolerance with a nonzero exit instead of printing it.

**The Codex diff review took three rounds to APPROVED — six findings, all real, three Major, all
in the seam's enforcement layer.** The common `meta=None` add path never triggered a module load,
so a bare library connection (raw sqlite3 + `findings.ensure_schema`, never `db.connect`) ran with
an EMPTY resolver registry and annotation was silently off — the runner now loads modules itself,
pinned by a fresh-subprocess test. The index-named savepoint was forgeable: a resolver could
commit and recreate `sp_pre_add_0`, turning the runner's own RELEASE into a commit of the
replacement transaction — savepoints are now nonce-named with a post-RELEASE transaction check.
The shared observation dict let a failing resolver poison the runner's `resolver_errors` stamp
and abort the very add the stamp exists to save — per-resolver deep copies, snapshotted outcomes.
Minors: `category=""` pooled the whole table (the accessor gained the `categories=` exact-value
tuple, mirroring `statuses=`), and `group_report`'s bare `== "all"` re-shipped CB-25's mock.ANY
trap inside a module written weeks after that rule was documented (type-pinned sentinel now).
Round 2 also caught main moving under the branch — the exact diff would have reverted CB-48.

**Two defects caught by running, not reviewing:** the trigram memo keyed (id, created_at) collided
across databases inside one whole-second timestamp (in-memory test DBs surfaced it; a re-created
tracker would too) — re-keyed by content; and the first `min_pair_score` implementation took the
minimum over recorded EDGES, which are ≥ threshold by construction and therefore hid the very
chaining it existed to expose — caught because the corpus run printed 0.701 where the review had
measured 0.393.

**Net change: CB-45 fixed; CB-46 stays open — its formal blocker is satisfied (the dry-run tool
exists) but the merge policy is still PROPOSED, not ratified (plan D6); no new cards filed.**
MCP server restart/pipx reinstall needed before the new annotate semantics are live — same
operational note as the identity iteration.

## 2026-08-16 — CB-43 + CB-44 (the identity function, and the seam that wasn't built)

User-directed pick ("Fix CB-44, then CB-43"), brainstormed interactively first — the stated
meta-goal was **preventing codebug number explosion**: grouped rich cards instead of N loose ones.
Plan: `.claude/plans/CB-44-CB-43-identity-dedup.md` (kept, with the full review appendix).

**The review inverted the iteration's shape, and both inversions were the user's call.**
adversarial-review-x2 (Opus + Codex/Sol attackers, Opus defender + judge) returned FAIL-REVISE
with 13 mandatory fixes, and two verdicts went back to the user as scope questions: (1) CB-44
closes as a **decision, not code** — approach A *is* the card's option (b), the drafted seam had
zero consumers and its outcome type could not express the semantics it would carry (both models
independently); (2) the iteration ships the **substrate only**, similarity as the next card.

**The headline empirical finding: the spec's fallback fingerprint collapsed 0 of 6,509… actually
3,158 corpus rows — including 0 of the 115-row family the card was filed about.** The Opus
adversary implemented it and measured; the defender reproduced the 0 and refuted the inflated
denominator and the "so ship similarity first" conclusion; the judge re-measured everything to the
row. The blocker was the branch slug — declared in every row's own meta, which is what the repaired
normalization now strips. Honest value: **71/115 of one family, ~2% corpus-wide**; the rest is
prospective (filer-supplied fingerprints are the primary path). A design premise died in review and
the fix was one function, not a re-scope — but only because the measurement was made BEFORE landing.

**The judge caught what neither attacker did:** with stale-reopen dropped, `stale` fell through to
*plain insert* — the branch table was not total, silently resuming the explosion for 43/115 rows.
Now `stale` is live-and-bumps, and `TestBranchTotality` pins totality over `FINDING_STATUSES`.

**The Codex diff review (after the design review, on the finished code) found 5 more, all real:**
reopen leaked capacity/ownership for items closed by `set_item_status` rather than the reconciler;
caller meta could poison the ring (`meta={"occurrences": 1}` → TypeError on the NEXT observation);
stripping every meta value merged rule_code E501/F401 defects (now volatile-KEY-scoped stripping,
erring toward split); CSV import dropped the meta column so derived fingerprints did not round-trip;
add stored stripped fingerprints while query bound them raw. Every fix carries a regression test
proven to fail against the pre-fix commit (11/11).

**Test-vacuity traps hit twice, both caught:** the batch-order test's first draft used ids whose
input order coincided with B-tree order (passed against the bug it targets); and the "prove it
fails on main" run silently tested the FIXED code — `[tool.pytest.ini_options] pythonpath=["src"]`
prepends the worktree's src ahead of `$PYTHONPATH`, so cross-tree proof runs need
`-o pythonpath=<other-tree>/src`. Worth never re-deriving.

**Also:** `test_claims.py`'s `_finding` helper wanted N distinct entities from N identical tuples —
under the ratified contract that is one identity, so the helper now varies its default description
(the contract-conformant fix, not fixture-whack-a-mole; explicit-id fixtures were already safe by
the explicit-id bypass). `CountingConn` counted only `conn.commit()` and forwarded no
`__setattr__` — the single-commit guarantee was pinned by a test that could not fail against
`db.txn`; it now hooks both seams.

**Net change: 2 closed, 2 filed (CB-45, CB-46, blocker-linked); open queue unchanged in size but
strictly better-shaped** — both new cards are ratified-scope follow-ups with measured targets, not
noise. 1004 tests pass (988 baseline + 16 net new), ruff clean. **The running MCP server holds
pre-identity code until restarted.**

## 2026-08-13 — CB-16

**Picked** over CB-15 (high, older) on blast radius: CB-16 silently destroys the investigation
record, which is the tracker's entire value. Codex/Sol agreed on the shortlist ranking.

**Not clustered.** CB-18 and CB-15 (expose `append_note` over MCP/CLI) were rejected as cluster
members — "wrapper missing a parameter" is a different transformation in a different layer from
"build meta once in the domain layer", so it passes none of the four clustering predicates.
CB-18's own text asks for the clobber to be fixed first, which is an ordering argument, not
atomicity.

**Reproduced before planning**, including the reqs.py twin the card had only read.

**Cross-model review of the diff (Codex/gpt-5.6-sol) found a regression I had introduced** —
hoisting the `json.loads` made every update depend on stored meta parsing, so a status-only
update on a malformed legacy row aborted before its own SQL. Fixed by parsing lazily; pinned by
a test that fails against the eager version. Codex also caught a vacuous `updated_at >=`
assertion. Both fixed before landing.

**Sibling sweep:** grep of every built `SET` clause in `src/` — only instance. Rule added to
`CLAUDE.md`.

**Left open, deliberately:** CB-15, CB-18 (MCP/CLI `append_note` surface), CB-17 (severity
immutable). CB-1 (March, "separate MCP servers") is a **stale-card candidate** — `server.py` and
`cli.py` already filter by `--mode`, so it may already be delivered; needs verification, not a fix.

**Note for the next iteration:** the long-running MCP server process holds pre-fix code in
memory until it restarts, so `update` calls made through it can still clobber. Use one
meta-writing argument per call until the server is restarted.

### Post-landing adversarial review (Codex / gpt-5.6-sol, three lenses)

Run after CB-16 landed, at the user's request. Lenses: code correctness, test quality, and
verification of the author's own report claims. Hardening merged as `63d0658`.

- **Code: clean.** No correctness regression in the shipped fix. Ordering, hook firing, commit
  ordering and the no-op path all unchanged; concurrency exposure identical to before.
- **Tests: the worst finding.** The structural guard asserted on `set_trace_callback` output,
  which expands bound parameters — so it produced false failures (a notes value containing the
  token `meta =`) and false passes (quoted identifiers). Both suites now assert on the SQL
  *template* via a `RecordingConnection`. The requirements twin was also materially thinner than
  the findings one and has been brought level.
- **Claims: two were overstated.** The sibling sweep did not enumerate `ON CONFLICT DO UPDATE SET`
  upserts (checked since — `claims.py` and `sweep.py`, both safe, conclusion unchanged). And the
  CB-4 closure claimed provenance "owns `head_sha`" when that function is a dead delegation.
- **The review found a third stale card, CB-5**, by checking `CLAUDE.md` against the code rather
  than the code against itself.

**Lesson worth keeping:** three of this tracker's April-2026 architecture-debt cards (CB-4, CB-1,
CB-5) described a codebase that no longer exists, and `CLAUDE.md`'s debt section had drifted with
them — each doc entry was repeating its card's false premise. Verify that section against the code
as a batch rather than discovering them one card at a time.

## 2026-08-13 — CB-18 + CB-15 (the update surface)

One tree, two commits, because CB-18 is exactly and only CB-15's first defect — dedup rather than
clustering. Plan: `.claude/plans/CB-18-CB-15-update-surface.md`.

- **CB-18 / CB-15(1)** — `append_note` existed in the domain layer but neither surface declared it,
  so every agent note edit took the destructive replace. Now plumbed to MCP and CLI; `notes` says
  REPLACES.
- **CB-15(2)** — an unknown argument *name* was dropped and the call reported success, while an
  unknown *value* raised. Cause: the SDK's argument model uses pydantic's default `extra="ignore"`.
  Fixed server-wide by a middleware refusing undeclared arguments.

**I nearly got this wrong.** My plan was to ship (1) and defer (2) as "needs SDK internals". The
bounded Codex pass rejected the split and pointed at the public `MCPServer.middleware` API I had
missed — deferring would have been the cheap-substitute failure the workflow exists to prevent. The
lesson generalises: *"the correct fix needs private API"* is a claim to verify against the library,
not a reason to shrink scope.

**Negative result worth not re-deriving:** `additionalProperties: false` does nothing here. The
server never validates arguments against the JSON Schema — confirmed by injecting it into a live
tool and watching the call still succeed.

**CB-17 deliberately excluded** despite Codex judging it cluster-eligible. It needs a core parameter
plus validation and carries real product questions (should retriage record actor/reason/history?).
The hostage test decided it: if CB-17 stalled on that decision, two finished edits would wait behind
it. It is now the only substantive card left — and its first question is for the user, not an agent.

## 2026-08-13 — CB-17 (severity became mutable)

**The "first question is for the user" call above was wrong, and worth correcting rather than
quietly dropping.** CB-17 was filed by the user, and its body already picks the shape ("Recommend
(a) plus a hook, if an audit trail is wanted"). The only genuinely open sub-question was the
conditional hook, and that answers itself: nothing consumes a severity-change event, so building
one would have been speculative surface. Direction was ratified in the card all along. The lesson
is narrow but real — *check whether the card's author already answered the question before
escalating it back to them.*

**Half the card was already fixed.** Its "unknown kwargs should raise at the MCP boundary" half was
CB-15, closed last iteration. That is now what makes the wire test meaningful: an undeclared
`severity` is REFUSED, so the test fails loudly against the old wrapper instead of passing while
the value is dropped. Two iterations compounding.

**Review placement, deliberately changed.** The standing rule sends a pre-implementation plan to
`adversarial-review-x2`. Here the design question went to Codex *before* the plan (it produced the
"file the case-asymmetry separately" decision), and the full pass went on the **finished diff**
instead — which is where this repo's last real regression was caught. It paid again: Codex found a
regression I had introduced (`except (KeyError, ValueError)` swallows `json.JSONDecodeError`, so a
committed write would report as bad input and exit 1) plus two test gaps that would have let a
broken implementation ship — the MCP test proved the argument was *declared* but never *forwarded*,
and every fixture held one row, so a missing `WHERE` would have passed all 12 tests.

**Mutation testing is now the standard here, not a flourish.** Three separate mutations — delete the
MCP forwarding, delete the CLI forwarding, revert the exception arm — each failed exactly the tests
that should catch them. Verifying the mutation *landed* mattered: an early grep discriminator
returned a misleading count because `query_findings` declares the same signature.

**Live instance of CB-15's failure mode, still running.** An `append_note` call through the session's
MCP server returned a success payload and changed nothing — the server process predates `987fc20`
and `d6ce8de`, so it has neither the parameter nor the strict-argument refusal. **The MCP server must
be restarted before `append_note` or `severity` are usable through it.** Until then, prefer `notes=`
and re-fetch to confirm every write.

**Verify subagent citations, but check your own greps too.** Two of the sweep agent's claims looked
false and were not: `grep -c actor` over `milestones/__init__.py` returns ~27 because the substring
lives inside `conn_factory`, and a `merge.py` window that stopped at 620 missed the parsers at
624–632. The agent was right both times; my discriminators were bad.

**Filed rather than fixed:** CB-19 (severity case-strict, sibling priority case-lenient — split on
Codex's advice because fixing it widens `add_finding`'s accepted inputs) and CB-20 (`ORDER BY
severity` is lexical, so `low` outranks `medium` in the primary read path — this card's own premise
one layer down). CB-6 upgraded from suspected to confirmed: the CLI is *systematically* a subset of
the MCP surface, not just missing blockers, so its real question is a policy one.

## 2026-08-13 — the CB-17 simplify pass, run after landing

**I skipped `/simplify` before landing CB-17.** Phase 8 mandates it; I went from verification
straight to commit. The user asked. Running it found five things worth fixing, so the skip cost
something real — record it as a process failure, not a footnote.

The tests were the cheap half (a fixture duplicated verbatim across two classes, two tests
subsumed by a loop, one testing the same guard twice). **The docs were the expensive half:** the
CLAUDE.md rule I had just written, "a column settable at INSERT should be settable at UPDATE",
**shipped false on the tree it was written for** — `description`/`category`/`file` are immutable
on findings while requirements can rewrite `description`. A Codex doc audit then found my
*correction* was also incomplete: `source` is INSERT-settable on **both** entities and in neither
update contract.

Three passes over one function, three different missing columns. That is the argument for CB-21:
enumeration by inspection does not converge, so the answer is a parity test, not more careful
reading.

**Codex reliability, worth knowing:** the broad five-part review prompt timed out at 600s, and two
`codex exec` CLI runs exited 0 after a single planning line (it reads stdin as extra prompt input,
which `nohup` leaves dangling; `reasoning effort: ultra` compounds it). A short single-question MCP
prompt worked first time, as every focused prompt has. **Keep cross-model prompts narrow.**

## 2026-08-13 — CB-20 (queues ordered alphabetically)

**The sibling sweep is what made this iteration worth more than its card.** The card named two
sites; sweeping every `ORDER BY` in the package found a third — `blockers.query_deferred_entities`
— and that is where the worse defect lived: `PRIORITIES` sorts lexically to `could, must, should`,
so the deferred queue put the **highest** priority **last**. The card's own "RELATED: reqs is also
lexical" lead was, meanwhile, **wrong** — both `get_stats` functions pre-seed their output dict
with the vocabulary, so row order never reaches the caller. Verify a card's leads before carrying
them; two of this one's three were false.

**Fixed at the registry, not the call site.** `EntityKind` already declared `sort_col`; its
precedence now sits beside it as `sort_vocabulary`, behind `order_by()`. The field is **required
but nullable** — a kind may legitimately sort by an id, but a *default* would let a future kind
with a TEXT vocabulary column inherit "no precedence" silently, which is the defect itself. That
choice is why adding it broke a claims test, and breaking that test was the design working.

**The mutation that mattered.** Codex, asked only for the fix shape, warned that the CASE
placeholders sit between the WHERE fragment and LIMIT/OFFSET, so their params must be spliced
there rather than prepended. Prepending fails **exactly one** test — the filtered-query one —
while all five unfiltered ordering tests pass. Without that single test the bug ships. A test
whose absence is invisible is the kind worth writing down.

**Also worth not re-deriving:** `ruff format` on a file you only appended to reformats the whole
file. `test_blockers.py` came back with 142 changed lines for a 50-line addition. Restore the file
and re-append rather than shipping the noise — `ruff check` is the gate, not `ruff format --check`,
and the repo has pre-existing drift in 25 files.

## 2026-08-13 — CB-22 (the allowlist that never checked itself)

**Picked against my own ranking.** CB-21 (medium) led on severity and blast radius; CB-22 (low) was
second. The bounded Codex pass over the shortlist put CB-22 first and it was right: CB-21's correct
fix includes making `description`/`file` mutable on findings, which widens the tracker's API — a
user decision, and Phase 3's explicit always-ask condition. CB-22 has none, and it lays the
fail-closed-at-construction pattern CB-21's parity gate wants. Sequencing, not substitution.

**The card was wrong twice, and only running it showed that.** It claimed `sort_col` was unguarded
on the vocabulary branch (both branches were covered) and that `_SAFE_IDENT` had no callers (its
grep was scoped to `src/`; a test used it). Meanwhile the defect it *understated* was the real one:
`readable_cols` was not merely a prospective syntax hazard — a member of `(SELECT meta FROM
findings)` passed the membership check and `field()` returned the `meta` column. **An allowlist
membership check guards the caller's argument, never the allowlist's own contents.** Two
obligations; only the first is visible at the query site. That is now a CLAUDE.md rule.

**The fix shipped with the defect it was fixing, until review caught it.** I lifted the pattern
`^[A-Za-z_][A-Za-z0-9_]*$` unchanged out of `_SAFE_IDENT` and into the new gate. `$` also matches
just before a trailing newline, so `is_sql_identifier("id\n")` was True and
`EntityKind(table="findings\n")` constructed cleanly. Codex/Sol found it on the finished diff.
**Anchor with `fullmatch`, never `^…$`.** Related: `entities._SAFE_IDENT is types._IDENT` returned
`True`, which reads as "already deduped" and is only `re` caching on the pattern string — a check
that would have talked me out of the dedup for a reason that isn't one.

**Two of five mutations were added because a mutation SURVIVED.** Truncating the member loop to its
first element passed all 28 tests: my payload `(SELECT …)` starts with `0x28` and sorts before every
real column name, so a check-one implementation still caught it. The test proved *some* member was
validated, not *every* member — fixed by parametrizing over payloads at both ends of sort order.
Codex found the other survivor: validating `sort_col` only when `sort_vocabulary is None`, which
every negative test happened to set, while both production kinds declare a vocabulary.

**Process failure worth recording:** I mutation-tested `capacity.py` before committing it, so the
`git checkout --` restore destroyed my own fix. The skill says commit first, mutate second, and it
says so for exactly this reason. Cost was one re-edit; the tests caught it in the same run.

**The sibling sweep paid again.** `group_by` in `findings.py`/`reqs.py` is caller-supplied straight
from MCP and correctly allowlisted — the scariest-looking site was fine. `milestones/capacity.py`
was not: `f"{size}_held"` guarded only by a CHECK two layers away, and the two branches disagreed
for the same input — `OperationalError` with a capacity row, and **a row of zeros plus a success
return** without one. A success-shaped return for a write that did not happen is the CB-15/CB-16
class of lie, found in a module this card never mentioned.

## 2026-08-13 — CB-19 (the vocabulary that only resolved on one side)

**The card scoped it to three sites. It was five, and the missing one would have made the fix
harmful.** `query_findings` resolved `status` and left `severity` raw, *two lines apart*. Routing
only the write paths — exactly what the card specified — would have stored `high` for
`add(severity="High")` while a read-back by the same spelling found nothing. The card would have
manufactured a silent wrong answer. Codex/Sol found it when asked, specifically, "is there a fifth
site I have not listed"; that phrasing is worth reusing, because "review my plan" would not have.

**The sibling sweep found the bigger, older defect.** Sweeping every vocabulary filter turned up
`query_requirements`'s `status`/`priority` and `embeddings.search_similar`'s `status`, all raw —
while the requirements *write* path has normalized through `resolve_priority` since it was written.
So `update_requirement(priority="SHOULD")` stored `should` and `query_requirements(priority="SHOULD")`
returned zero rows, **live, in the sibling entity, predating this card entirely**. Worse than the
findings case the card was filed for, where a bad severity was at least rejected loudly on write.
The general rule is now in CLAUDE.md: *a vocabulary must resolve on both sides of the entity.*

**A tripwire fired, exactly as its author intended.** `test_invalid_severity_raises[HIGH]` carried
the docstring "pins the case-strictness that CB-19 proposes to relax, so relaxing it cannot happen
silently." That is the pattern worth copying: when you deliberately ship a strictness you expect a
future card to relax, pin it with a test that *names the card*. It cost nothing and it converted a
silent contract change into a deliberate one.

**Review of the finished diff caught two regressions, one of them mine and one pre-existing.**
`_resolve` called `.lower()` before checking anything, so `severity=None` escaped as `AttributeError`
instead of the contractual `ValueError` — fixed at the shared helper, not per resolver. And
`_cmd_query`/`_cmd_reqs_query` never caught `ValueError` at all, so an unknown `--status` printed a
raw traceback and leaked the connection. **I checked whether that was mine before claiming it**: it
was not — `acf520b` already resolved `status` with no `try` in the handler. Worth the two minutes;
the honest framing is "pre-existing hole I widened", not "regression I introduced".

**Verification trap, hit and worth not repeating twice.** Testing the old behaviour by pointing
`PYTHONPATH` at a worktree of `acf520b` produced a traceback citing `/home/faxik/w/codebugs/src` —
the editable install resolved `codebugs` to the *main* checkout, so the test proved nothing. This is
the same trap CLAUDE.md documents for `dump_schema.py`. `git show <ref>:<path>` is the reliable way
to read old behaviour.

**Also: the mutation loop timed out mid-run and left a mutation applied**, and a second time a
`git checkout --` restore during mutation testing destroyed an uncommitted fix. Both are the same
lesson the skill already states — commit before mutating — and the second occurrence means it should
be a habit, not a rule to re-read. Run mutations against targeted test files, not the full suite:
five full-suite runs is 5 minutes and blows a 2-minute timeout, five targeted runs is 50 seconds.

## 2026-08-13 — CB-24 (the read and the write were two transactions)

**The queue was NOT blocked after all.** The previous row closed with "every remaining card needs a
product decision", which was true when written and false forty minutes later: a Codex review of the
day's range filed CB-23/24/25/26 at 15:40. A "queue exhausted" verdict has a shelf life — re-read
before believing your own last row.

**The existing suite caught the regression I introduced, and that is the most useful thing that
happened.** Moving the body into `db.txn` put the closing `row_to_dict` inside the block, so a
`JSONDecodeError` from malformed stored meta rolled back a write CB-16 guarantees lands. Three tests
failed immediately — the ones CB-16 and CB-17 left behind for exactly this. **The conversion must sit
outside the block; the SELECT stays inside.** Worth noting the shape: a fix for one silent-write bug
re-created a different silent-write bug two cards old, and only the earlier card's tests saw it.

**`conn.commit()` had to be deleted, not moved.** `db.txn` is reentrant and yields `False` under an
ambient transaction; a surviving commit would commit the *caller's* work from inside a nested call.
`milestones.triage_dismiss` is such a caller, and it *gained* atomicity from the change. The card
proposed the right fix and did not mention this, which is the part that would have broken things.

**Codex on the finished diff found four real gaps, and the sharpest was in my tests.** The
concurrency tests could **false-pass against the unfixed code**: the only guard was a 1.0s timeout, so
a worker thread not scheduled inside that second let A write first, after which B read A's committed
row and merged cleanly. Fixed with a `b_started` handshake set after B's connection is open. Also:
`BEGIN IMMEDIATE` sat above the vocabulary resolvers, so under contention an invalid status raised
`OperationalError` after five seconds instead of `ValueError` immediately (resolvers are pure
functions of their arguments — they moved out); my "converted AFTER the transaction commits" comment
was **false in the ambient case**; and there was no requirements twin of the ambient-commit guard.

**The sibling sweep turned one fix into three.** `milestones/closegate.mark_branch_only` and
`milestones/triage.triage_promote` both merge `meta_json` in Python with no transaction — neither
mentioned by the card. `triage_promote`'s duplicate-attachment check is also a check-then-act that
two concurrent promotions could both pass; the wrap closes both. Verified NOT instances, which is
half the value of a sweep: `capacity.py` and `sweep.py` use SQL-side `col = col + 1`, `claims.py`
uses `ON CONFLICT DO UPDATE`, `pull_next` and `merge` already sit in `BEGIN IMMEDIATE`, and
`mark_integrated` writes literals. **A one-statement read-modify-write is not an instance.**

**/simplify earned its place again** — and this time it was run *before* landing, unlike the CB-17
iteration. The reuse and simplification agents converged independently on ~130 lines of duplicated
thread scaffolding (extracted to a per-class `_interleave`), and the reuse agent correctly *declined*
to consolidate `PausingConnection` across files after reading the no-`conftest.py` rule first. The
altitude agent found the two milestones sites carried the same "do not restore `conn.commit()`"
docstring as findings/reqs **with no test behind it** — prose on two of four sites, a real guard on
the other two, the CB-17 asymmetry again — and that the changelog's "`triage_dismiss` gains
atomicity" claim was asserted but never exercised *through* `triage_dismiss`. Both now have tests,
and both tests kill a mutation.

**I destroyed my own uncommitted work with `git checkout --` during mutation testing. Third
occurrence.** The two rows above already record it twice. The cost was re-applying two `reqs.py`
edits. It is not a rule that needs re-reading — the mutation runner should refuse to run on a dirty
tree, which is the only version of this lesson that will actually hold.

**Left open:** CB-23 (high, and the only card whose fix is blocked on a decision rather than on
work), CB-21, CB-25, CB-26, CB-6, and the newly filed CB-27.

## 2026-08-13 — CB-23 (the directory is not the tracker)

**The question was decidable, and framing it as a semantics choice was the mistake.** The card
offered three options and the author's first reply was "I'm not sure which is better — what are we
defending against, and how does an empty `.codebugs/` even arise?" Answering those two questions
empirically collapsed the choice: `_db_path`'s and `_declared_db_path`'s **own docstrings already
promised** that a named or declared root "must fail loudly rather than quietly become a second, empty
tracker", while the code checked only `os.path.isdir`. So option (c) was not a new decision at all —
it was code diverging from a rule ratified in CB-11. **Before presenting options as a values choice,
check whether one of them is already the stated contract.**

**How an empty `.codebugs/` arises, since that was the load-bearing question.** `init_project` is
the package's only `os.makedirs` and it runs *before* the database is created, so a Ctrl-C'd `init`
leaves exactly this state. That is the common, benign cause — and it is why the walk keeps
self-healing. The dangerous causes are all *named* paths: a `--repo` typo, a stale exported
`CODEBUGS_ROOT`, a collaborator with `*.db` in a global gitignore. **The discriminator is not "does
the directory exist" but "how did we come to be pointed here"** — a walk is evidence, a named path
is an assertion.

**The first attempt broke 38 tests and 305 errors, and that was the design telling me something.**
`init_project` created its database *by way of* `connect()`, so tightening the resolver broke the one
caller that must create. The fix is the split: `_open(path, create=)` is the only opener,
`connect` = `_resolve_db` + `_open`, and `init_project` calls `_open` directly. That is what makes
"init is the only creator" structural rather than incidental.

**Codex found that my fix reproduced the bug it was fixing.** `isfile` in the resolver and
`sqlite3.connect` in the opener is a check-then-act: another agent removing the database in between
gets a fresh empty one built for it. The named and declared routes now open through SQLite's
`mode=rw`, so existence is enforced *by the open*. The path check stays only for its message.
**A fail-closed gate implemented as a separate check is not closed.**

**Codex also found the case that makes the asymmetry non-benign — and the suite already modelled
it.** In the CB-13 `--separate-git-dir` layout the mis-bound root is a stray `.codebugs/` *directory*,
so `codebugs where` printed a database that does not exist as the project's tracker, and the MCP
preflight stayed silent. `describe_root` now reports `exists` separately from `error`, because
**resolving is not the same as being there**. That is the one case where the preflight speaks on an
ordinary discovery binding.

**I wrote a CLAUDE.md paragraph that contradicted itself two sentences apart** — "init_project is the
only function that creates one", then "the walk creates the database inside it", then "`connect()`
never creates; only `init_project` may call `_open`". `_open` has exactly two call sites. The altitude
agent caught it. The repair was not better prose but a **ratchet** (`TestOpenCallSitesRatchet`),
following the `BEGIN IMMEDIATE` allowlist pattern — and the true rule is statable in one sentence
once you name the right noun: *`init_project` is the only function that creates the `.codebugs/`
**directory***.

**`git checkout --` destroyed uncommitted work during mutation testing. FOURTH occurrence today.**
The CB-24 row already said the lesson is not a reminder but a guard. Writing it down twice has now
failed twice. **The next iteration that touches this workflow should make the mutation runner refuse
a dirty tree**; until then, commit before every mutation without exception.

## 2026-08-13 — CB-25 (a falsey value is wrong input, not "no filter")

**The fix nearly reintroduced the bug it was fixing, and only a cross-model review caught it.**
The obvious predicate for "is this a real filter" is `value is not None and value != ""`. That runs
arbitrary user code: `unittest.mock.ANY` is truthy yet compares **equal** to `""`, so it would have
flipped from *raises today* to *silently disables the filter* — CB-25's exact shape, inside CB-25's
own fix. A `str` subclass overriding `__ne__` does the same to a perfectly valid `"open"`. The
predicate is therefore type-based and uses `str.__len__` rather than `len()`, and the two tests that
pin this are the whole value of the test class: **mutation-tested, the naive predicate passes 10 of
12 predicate tests and fails only those two.** A guard against running user code cannot itself run
user code.

**My sibling sweep was an enumeration, so it could not converge.** I grepped
`if status:|if severity:|if priority:|if kind:|if state:` — the names of filters I already knew were
affected. That is structurally incapable of finding a filter I did not already know about, and it
missed `blockers.query_blockers(trigger_type=...)`, where the `TRIGGER_TYPES` validation sits
*inside* the truthy guard (which is exactly what makes it look safe on inspection). Grepping the
**shape** — `if <name>:` wrapping a vocabulary membership check — finds it in one pass and finds
nothing else, which is what makes the sweep provably complete rather than merely long. This is the
repo's recurring lesson (CB-21, CB-22, CB-27) arriving in the sweep methodology itself: *a check
that validates elements cannot validate their composition, and a rule written as a list is the
letter.* The /simplify altitude pass caught it, not me.

**I duplicated a vocabulary in the commit that argued against duplicating vocabularies.** I declared
`MERGE_SESSION_STATUSES` while `types.MERGE_STATUSES` already existed — byte-identical, and dead
code, which is *why* `get_sessions` validated nothing. Three copies of one four-value vocabulary,
with a comment above them about single sources of truth. The right fix was to use the constant that
already existed, which also revived dead code. **Before declaring a constant, grep for its value,
not just its name.**

**The pick was Codex's, not mine, and it was right.** I ranked CB-21 (medium) above CB-25 (low) on
severity × blast radius. Codex pointed out CB-21's parity gate cannot assert CLI parity without
first answering CB-6's unresolved policy question, so CB-21 trips the product-decision predicate
rather than being a clean pick. It also correctly reduced CB-25's claimed blast radius — pydantic
rejects non-strings, so MCP and CLI are unreachable and only direct Python callers are affected.
Both corrections survived my own verification.

**Left open, and the queue is now fully decision-blocked:** CB-6 (CLI-peer-or-subset policy — this
is the keystone; CB-21 depends on it), CB-21, CB-26, CB-27, CB-28, CB-29. Nothing remains that can
be shipped without a product decision.

## 2026-08-13 — CB-28 (a declared argument discarded by routing)

**My plan was rejected by cross-model review, and the rejection was correct.** I proposed raising
at every site, arguing that forwarding the filters would teach the generic `blockers` module what
`severity` and `priority` mean. That attacked a strawman implementation. The review produced the
evidence: `docs/2026-04-04-blockers-design.md:278-291` **already specifies** the clean shape — strip
the pseudo-status, get the deferred ids from blockers, pass them as an id restriction to the owning
domain's query — and `blockers.get_deferred_item_ids` had **already been written** for exactly that
and was simply never called. `provenance.check_findings`' own docstring had promised the same thing
for just as long. So "raise" was a cheaper substitute for a fix the repo had already designed, which
is the precise failure the bugfix workflow's Phase 2b exists to prevent. **Before concluding the
correct fix is infeasible, grep the design docs — this repo writes them and then forgets them.**

**The feasibility worry I never wrote down was ordering**, and it evaporated on inspection:
`query_findings` and `query_deferred_entities` both order by `{rank_sql}, created_at DESC`. An
unstated objection cannot be checked, which is why Phase 2b says to write the correct fix down
*first*.

**The fix contains the same trap as the last two cards, one layer along.** An empty deferred
intersection must not be forwarded as `ids=[]`, because every domain query reads an empty list as
"no filter" and would return the whole table — CB-28's defect reappearing inside CB-28's fix,
exactly as the naive predicate reintroduced CB-25 inside its fix. Three iterations running, the
dangerous line has been *the fix's own edge case*, not the original defect.

**Two of the six sites came from the review, not from my sweep**, and the sweep needed two AST
passes because pass 1's notion of "a branch" was `return` — which structurally cannot see
`provenance`'s if/else assignment. Even both passes missed the conditional-query-building shape
(`meta_value` without `meta_key`). Last iteration's lesson was "sweep for the shape, not the names";
this one refines it: **enumerate the shapes a branch can take before trusting a shape sweep.**

**Process miss, recorded because it cost a real verification.** I committed straight to `main`
instead of branching, so the first mutation attempt (`git checkout main -- src/`) was a **no-op**
and reported 20 tests passing against "unfixed" code. That is precisely the vacuous-mutation trap
the workflow warns about, and it was caught only because the diffstat was empty. Re-run against
`f22d9fb` showed 9 of 20 failing, with the other 11 being genuine behaviour-preservation controls.
**Always check the mutation diffstat before reading the test result** — and branch first, which
would have made the no-op impossible.

**Also worth knowing:** backticks in a `git commit -m` message get shell-substituted. Two words
vanished mid-sentence from the first commit; amended via `-F` from a file.

**Left open:** CB-6 (CLI-peer-or-subset policy, the keystone — still unanswered and still gating
CB-21), CB-21, CB-26, CB-27, CB-29. `blockers.query_deferred_entities` is now called only by its own
tests and is marked SUPERSEDED rather than deleted, because those tests pin the ranked ordering the
forwarded path must also produce.

## 2026-08-14 — CB-26 (re-scoped): terminal source entities left live in derived queues — LANDED

Focus `codebugs`. Branch `fix/cb-26-triage-projection-reconciliation`. Card claimed `in_progress`.

**The ledger's own "fully decision-blocked" conclusion, recorded twice, was FALSE** — and this
iteration exists because a Codex triage pass said so and was right. Each card mixes three separable
things: an invariant, a product choice, and a proposed enforcement mechanism. The previous two
iterations let an unresolved *mechanism* or *product choice* block the *invariant*, and so skipped
live defects. CB-26 was skipped as "a design question" while its projection was measurably broken;
CB-27 was filed as prospective ("about the fifth site") while the fifth site already existed.

Verified before picking: 19 of 23 open `stream/triage` items point at terminal findings; the only
`register_status_change_hook` in the package is `claims.py:825`; milestones registers add-time
routing only (`milestones/__init__.py:638`).

**Also corrected on the cards this iteration:** CB-6's headline premise is stale (blockers HAS had
CLI commands since 5fffe4d) and its 2026-08-13 "CONFIRMED" note re-confirmed the wrong clause;
CB-27 re-scoped with the live `sweep.mark_items` site; CB-26 raised low → high.

**My own triage error, recorded because it is the reusable lesson:** I proposed CB-6+CB-21 as one
tree on an "atomic landing" predicate. It does not hold — CB-21's argument-parity axis does not
depend on CB-6's operation-coverage question, so that was grouping by theme wearing a predicate's
name.

**LANDED** as merge `004027e`. 914 tests pass (886 baseline + 28 new), ruff clean. The backfill was
RUN, not merely shipped: `milestone-reconcile --apply` closed 19 rows (18 `fixed`→done, CB-10
`wont_fix`→dismissed); `triage_inbox` went 23 → 4; a re-run reports "(nothing to reconcile)".

**The reusable lesson, and it invalidates two prior ledger entries.** Each card mixes three
separable things — an **invariant**, a **product choice**, and a **proposed enforcement mechanism**.
Both previous iterations treated any unresolved element as blocking the whole card, and so recorded
"the queue is fully decision-blocked" while two cards concealed live, reproducing defects. CB-26 was
skipped as "a design question" (its *title* asks one; its *projection* was measurably broken), and
CB-27 was filed as prospective — "this is about the fifth read-modify-write site" — when the fifth
site already existed in `sweep.mark_items`. **Triage the invariant, not the card's proposed
solution.**

**Both adversarial reviewers FAILED the first plan, on the same top finding**, and it was one I had
not considered: mapping `fixed → done` in a RELEASE milestone would silently weaken
`milestone_close`, whose unfinished gate reads only item status while `done_commit` is never read as
a gate at all — and `worktree-finish.sh` flips the finding to `fixed` *before* its non-fatal
`mark_integrated` step, so that refusal is the only thing catching a missed integration. Scoping the
hook to `kind='stream'` costs nothing today (zero non-stream items exist) and is CB-32.

**Three iterations running, the dangerous line has been the fix's own edge case — that streak now
holds at four.** Here it was capacity: closing a *pulled* item without decrementing would leak an
agent's slot permanently. And the review found a second one I had written into the plan as a virtue:
"an unmapped status raises, the hook logs it, so it stays open — which is visible" is **false** in an
MCP context, where stderr never reaches the caller and the tool still returns success. That is
CB-15's success-shaped lie wearing a fail-closed label; the failure is now an audit row.

**Process note.** The first Codex invocation burned 15 minutes producing nothing: `codex exec` with
the prompt as a positional argument printed "Reading additional input from stdin..." and exited 0.
**Pipe the prompt on stdin** (`codex exec ... < prompt.md`), and do not pipe its output through
`tail` — that buffers everything until exit, so a hung run looks identical to a silent one.

**Also corrected on the cards this iteration:** CB-6's headline premise is stale (blockers HAS had
CLI commands since 5fffe4d) and its 2026-08-13 "CONFIRMED" note re-confirmed the wrong clause;
CB-27 re-scoped with the live `sweep.mark_items` site; CB-26 raised low → high.

**Filed:** CB-30 (release_item double-decrement race + terminal transitions not centralized),
CB-31 (no shared "live items" seam; also the N+1 inside `pull_next`'s `BEGIN IMMEDIATE`),
CB-32 (release-milestone reconciliation + the `done_commit` close-gate question),
CB-33 (multi-attachment ambiguity in `_get_item_by_ref`),
CB-34 (blocker-derived deferred queue has the same gap),
CB-35 (CB-26's original severity-re-routing question, carried forward so closing CB-26 did not bury it).

**Left open:** CB-6, CB-21, CB-27 (live `sweep.mark_items` fix — the strongest next candidate, and it
needs no decision), CB-29, CB-30–CB-35.

## 2026-08-14 — CB-27 + CB-30 (both re-scoped): CB-24 conformance, and the population problem — LANDED

Focus `codebugs`. Branch `fix/cb-27-cb-30-txn-conformance`. Merge `ae77cba`. 923 tests pass
(914 baseline + 9 new), `ruff check` clean.

**The headline is not the fix, it is the count.** The queue presented as two cards about
read-modify-write atomicity. A mechanical sibling sweep — `grep -rn "conn.commit()"` (43 executable
sites) against `grep -rn "with db.txn"` (7 users), then reading every committing function — found
**19 instances of the CB-24 shape, 13 of them unfixed**, in `blockers.py`, `merge.py`, `sweep.py`
and three milestone modules that no card had ever named. CB-24 fixed four; CB-27 was filed as
"nothing stops a FIFTH". Both were undercounts by an order of magnitude. Filed as CB-36 (`high`).

**Two of fifteen shipped, and that is sequencing rather than substitution** — each wrap is
independently landable, so all of them would be ~13 independent edits against a clustering ceiling
of 4. The rest are recorded with `file:line` and reproducers, not deferred silently.

**Both cards were re-scoped BEFORE the tree existed**, so each could actually close. CB-27 → the
live site only, enforcement carried to CB-37. CB-30 → fault (1) only, fault (2) carried to CB-38.
Doing this after the fact is what left CB-30 "unclosable" in the first place, and the Codex reviewer
pointed out that closing CB-27 on its live half without the same split would repeat it.

**The review paid for itself, and the defender conceded 19 of 23 findings with 0 clean defends.**
What cross-model review caught that I had wrong:
- **The malformed-`meta` test was unreachable.** `_get_item_by_ref` parses JSON at the *top*, before
  any write, so "the write lands and the error surfaces" could not be produced by moving only the
  closing conversion. Needed a raw-row lookup.
- **Returning by numeric `id` does not fix CB-33.** I claimed it did. It buys self-consistency only.
- **Three proposed tests could not fail against `main`** — the failure mode this repo has shipped
  repeatedly. Two race tests would have deadlocked on `busy_timeout`; the multi-attachment test was
  vacuous because both lookups pick the same newest row.
- **My batch-transaction rationale was false.** Per-item transactions *would* close the per-item
  race; the real reasons are batch atomicity and pinning the once-only lifecycle/DAG read.
- **The plan contradicted itself on the deferred count** (ten vs thirteen).

The judge then found two more that were still wrong after that rewrite: `sqlite3.Row` has no
`.get()` (would have raised `AttributeError` on the first run), and the rewritten CB-30 race test
had inherited the old reproducer's preconditions — the hook is stream-scoped and
`_auto_route_finding` hardcodes `size='triage'`, so it would have been vacuous on both sides.
**That is the section-local fix-application failure in miniature: the "tests that cannot fail"
correction recurred one section later, inside its own fix.**

**Process notes.**
- The Opus adversary ran **one hour** without returning and was killed; it had gone off applying the
  change to a scratch copy and running full suites. Recorded as a single-attacker review. The
  defender and judge were Opus, so cross-model diversity survived, but coverage did not — the judge
  discounted its confidence to 0.82 for exactly this, and noted that a second attacker running
  suites is precisely what would have surfaced both mandatory fixes first.
- `ruff format` is **not** an enforced gate here — 27 files on main are already non-conformant — so
  it was deliberately not run. `ruff check` is the gate.
- A `cd` to main persisted across Bash calls and nearly committed worktree work to main. It failed
  safely; absolute `git -C` from then on.

**Found by the author, missed by both reviewers:** `pull_next` (`capacity.py:249`) has the same
post-commit re-read window as `release_item`. Filed as CB-39 — the judge made filing it a
precondition of implementing, so the two functions in one file would not sit in a CB-17-shaped
asymmetry with only one of them tracked.

**Net change: 2 closed, 4 filed (CB-36, CB-37, CB-38, CB-39); open went 10 → 12.** The queue grew,
and it grew for the right reason: every new card is either a verified defect population the previous
enumeration had missed, or a decision that was buried inside a card claiming to be a bug. That is
the review machinery working, not noise. CB-36 (`high`) is the strongest next candidate and needs no
decision.

## 2026-08-17 — CB-71 (the card's own prescription was the bug, and my test plan could not see it)

**Two reviewers, ten FATALs between them, and revision 1 did not survive.** An Opus adversary
(5 FATAL / 5 SERIOUS / 4 WEAKNESS) and Codex/Sol (4 FATAL-equivalents) ran in parallel against the
plan. Five findings were corroborated across both models, which is the number worth keeping: the
4-edit ceiling breach, the findings connection leak, `tests/test_reqs.py` having no CLI harness at
all, my sweep-completeness claim being false, and the `import_markdown` whole-call guard being safe
only by accident. Corroborated across two model families is a different confidence class from one
reviewer's opinion, and all five were conceded.

**The finding that changed the design, and only one model found it.** The Opus adversary noticed that
the pre-existing `except (ValueError, json.JSONDecodeError)` **spans the success `print`**, which runs
after `import_csv` commits. So a post-commit `ValueError` from that statement was already being
laundered into an input error — one tidy line, exit 1, write landed. My plan had spent twenty lines
reasoning about exactly that failure mode *for `OSError`* and never noticed `ValueError` reaches the
same statement through the same `try`. Worse, it explicitly **rejected** hoisting the print, which is
the fix. Codex missed this entirely; it is the whole argument for cross-model review.

**But the adversary's reproduction was wrong, and checking rather than accepting it mattered.** It
claimed closing fd 1 gives `ValueError: I/O operation on closed file` through the arm. Measured: fd 1
closed gives `OSError [Errno 9]` (a traceback — not laundered) or exit 120 at interpreter shutdown
outside every handler. Only closing the `sys.stdout` **object** produces the `ValueError` the arm
catches. So the defect is real and the reachability is narrower than reported — and establishing the
exact mechanism is what gave the test a runnable reproducer. **Verify the finding, not just the
verdict.**

**The vacuity failure was structural, not a missing case.** `"Traceback" not in stderr` passes
*identically* for the localized guard the plan mandates and for the handler-wide guard it calls a
CB-15 lie. Every test I proposed was satisfied by the design I was rejecting — so nothing could fail
when the load-bearing half broke. The repo already owned the template
(`TestRetriageCliContract` asserts `"Traceback" **in** stderr` so stored corruption is not disguised
as input error) and I had not looked for it. New spelling of a rule this repo keeps relearning:
**a check that validates elements cannot validate their composition** — here, a set of tests that all
agree with both designs cannot decide between them.

**Four cards filed because the plan asserted bookkeeping that did not exist.** Revision 1 said the
pipe defect was "**Filed as its own card**" and evidence was "appended to CB-55 instead". Neither was
true — the adversary checked the tracker and found no CB-76 and CB-55 untouched with
`updated_at == created_at`. That is the CB-48 shape (a document asserting the opposite of what is on
disk) inside a plan whose subject is success-shaped lies. Now real: CB-76, CB-77, CB-78, CB-79, and a
CB-55 append. **A deferral that is not written down is a dropped defect wearing a scope decision's
hat.**

**The ceiling is what governs a sibling sweep, not the predicate.** Both reviewers said five edits
breach `bug-clustering.md`'s hard stop of 4. The adversary also confirmed the part I had right:
unfiled sibling sites need no clustering predicate (`bugfix-loop` exempts sweep hits from the card
count, and predicate 3 must not be invented here), so the *count* was the only live objection.
Grouping the five "by file" into three rows was a regrouping, not a count — the plan's own next
sentence ("each row lands and verifies alone") proved it. Split to the input side; exports to CB-76.

**Found by the end-to-end diff read, missed by both reviewers and by 1280 tests:** the comment I
added to `findings.py` cited the loop's stderr prints at `:1866/:1877/:1886`, and my own 14-line
insertion had shifted them. A stale citation created by the very commit that added it. Fixed in
`19d5e95`. The one-artifact read is worth its cost.

**Net change: 1 closed (CB-71), 4 filed (CB-76, CB-77, CB-78, CB-79). Open went 32 → 35.** The queue
grew, and the interpretation is the same as previous iterations: every new card is a *reproduced*
defect that the previous enumeration could not see — two because the sweep's regex was the wrong
shape, one because a deferral had never been written down, one because a semantics decision was
hiding inside a card about identity. That is the review machinery working. It is not noise, and it is
not padding: CB-78 is `medium` and names a real product decision.

## 2026-08-17 — CB-75 (the guard was bypassable, and one cheap review found it)

**A single-file, six-line guard qualified for skipping adversarial review, and reviewing it anyway
was the right call.** `bugfix-loop` permits skipping x2 for a genuinely mechanical fix (single file,
<30 lines, no new API, no gate) and this met every criterion. I ran one Codex-only *diff* review
instead of the full x2 — proportionate — because `bench.py`'s guard family had already burned this
repo twice (CB-74 was reintroduced *inside* its own fix). It returned at 0.98 and the single most
important finding was that **my guard did not work**: `isinstance(csv_data, str)` is spoofable via a
`__class__` property, so the `TypeError` CB-75 exists to eliminate still escaped. Third iteration in
a row where the defect survived the first draft of its own fix.

**Reachability decided the severity of that finding, and it is not a contrived threat.**
`unittest.mock.MagicMock(spec=str)` sets `__class__` to `str`, so any test double passed to
`import_csv` defeats an isinstance guard. This repo already pins a mock-shaped trap for the same
reason (CB-25's `mock.ANY` case), which is the precedent that made it worth fixing rather than
documenting.

**The rule worth keeping, because it is now the second spelling of the same lesson:** *the guard's
predicate must be identical to the consumer's requirement.* CB-74 was "validating one view while
consuming another is not a guard" (list `__iter__` vs `__getitem__`); this is the same failure with
a spoofable type view — `StringIO` checks the real type, so the guard must too. Stated as a rule in
the code so the next guard does not re-derive it.

**Three false claims, all mine, one inherited.** (1) The tests claimed `match=` was "anchored whole
with re.escape". It is not: `pytest.raises(match=)` runs `re.search` and `re.escape` adds no
anchors, so a message with junk prefixed or appended would have passed. Now exact equality on
`str(excinfo.value)`. **The landed CB-72 class carries the identical false claim** — corrected in
place rather than left to mislead the next reader who copies it. (2) The class docstring said every
refusal case raised `TypeError` on main; `None` did not. (3) The CHANGELOG called this "the last
door in that family" — falsified in the same review, and now CB-81.

**Self-inflicted, and the second iteration running:** a comment citing `bench.py:164` went stale
because this diff added 45 lines above it. Last iteration's version of this was in `findings.py`.
Two occurrences in two days is a pattern, not bad luck — **stop putting line numbers in comments;
describe the site.**

**What the sweep did right this time.** Swept by SHAPE, not by name, which is the lesson CB-71's
retracted sweep claim bought: `io.StringIO(|csv.DictReader(|csv.reader(|json.loads(` over the
package, then reading every domain entry point taking a payload. That correctly **excluded** ~25
`json.loads` sites parsing stored DB rows — rewrapping stored corruption as `ValueError` is the
CB-15/CB-24-consequence-2 lie — and found two genuine siblings in the batch entry points, including
the nasty one where a `str` payload is *iterable* and so silently iterates characters instead of
failing at the loop.

**One card, not three, for those siblings.** Filing door-by-door is exactly how CB-24 → CB-27 →
CB-36 went 4 → "a fifth" → 19. CB-80 is a population card carrying the sites *and* the design
question (share `import_json`'s landed validator vs copy it a third time), because the answer
crosses module boundaries and is therefore not a bugfix.

**Net change: 1 closed (CB-75), 3 filed (CB-80, CB-81, CB-82). Open went 35 → 37.** CB-81 is the
one to note: `medium`, CLI-reachable, and a *plain data file* triggers it — a duplicate row label
kills `bench-import` with a raw `IntegrityError` traceback after rows have already been inserted on
a caller-owned connection. That is the same user-facing shape as CB-71 and strictly more likely to
be hit than CB-75 was.

## 2026-08-17 — CB-81 (the card's own headline was false, and review measured it)

**Focus:** `codebugs`, operator-restricted to pure bugfixes, simple first, batch if possible.
**Disposition:** fixed. **Merge:** `db92cdd`. **Follow-up:** CB-87.

**Batching was rejected, and the first reason I gave for it was invented.** Revision 1 of the plan
cited `BATCH-codebugs-dryrun-2026-08-17.md` as having run the clustering exercise "over this exact
backlog". Both attackers checked: the file was **untracked** (so not in the worktree and not
travelling with the merge), it covers 24 rows and not 35, and it mentions neither CB-77 nor CB-81.
Its conclusion was right and its authority was fabricated. The rejection now stands on the hostage
test applied to the two cards directly — CB-77 needs a decision (one outer transaction vs explicit
partial-success reporting) and a finished CB-81 would have sat waiting on it.

**The load-bearing correction: CB-81's TITLE is false, and my plan built its root cause on it.**
"IT HAPPENS AFTER ROWS HAVE ALREADY BEEN INSERTED" — measured, it does not. `db.connect()` returns
`isolation_level=''`, the CLI handler ends in `finally: conn.close()` and the MCP wrapper uses
`with conn_factory() as conn`, so both discard the implicit transaction: a failed `bench-import`
leaves `runs=0 results=0`. The card's *body* was careful ("partial **uncommitted** state"); the
title escalated it, my in-memory reproducer never closed its connection and so reproduced the
escalation, and I reported that escalation to the user before review caught it. **A harness that
does not close the connection cannot make a claim about what the user sees.**

**Second fabrication, mine: `--run-id` is not a `bench-import` flag.** Revision 1 called an explicit
run-id collision "a fifth user-visible door" and quoted a CLI invocation that exits 2 at argparse.
The guard stayed, rescoped honestly to library callers — and it earned its place anyway, because
Codex found that a *generated* id can collide through `CAST` saturation.

**Both attackers were necessary and they found disjoint things.** Opus ran the actual CLI and found
the two FATALs; Codex reasoned from source and found the saturation collision plus the test-vacuity
problems (my verification table demanded `runs=0 results=0` for a case that legitimately requires a
first successful import). One Codex finding was **rejected by measurement**: `UnicodeEncodeError` is
a `ValueError` subclass, so a lone-surrogate label was never the traceback it claimed.

**The card's letter was not followed, deliberately.** It said to refuse duplicate headers and
duplicate row labels. Both would reject payloads that import cleanly today (measured: a repeated
label with disjoint cells stores 2 values; a duplicate header with blank cells stores 0). The check
is on `(row_label, metric)` pairs — the UNIQUE constraint evaluated earlier — which narrows nothing
and still catches both of the card's cases. Intent over letter.

**Process failure worth recording: a `git add -A && git commit` ran in MAIN, not the worktree.** A
previous command had `cd`'d into the scratchpad, the harness reset cwd to the session root, and the
next relative-path git command therefore targeted main — committing three stray plan notes under a
message describing worktree work. The content is what main permits and the commit was unpushed, so
the message was amended (`b7459ee`). **This is the exact trap CLAUDE.md warns about at the end of
`worktree-finish.sh`, arriving through a different door: not "the worktree was removed", but "a
scratchpad `cd` reset the cwd".** Use `git -C <path>` for every git command during an iteration,
not just after the finish.

**Sibling sweep, by shape:** "a database constraint is the only payload check on a CLI-reachable
write" — `bench-import` was the **only** instance. `sweep-create` pre-checks by name, `sweep-add`
deduplicates, `findings.add` pre-checks its partial unique index, `reqs-add` takes no caller-supplied
id. That is a real result, not an empty one. The one genuine sibling came from the review, not the
sweep: CB-87.

**Net change: 1 closed (CB-81), 1 filed (CB-87). Open went 35 → 35.** CB-87 is the interesting one:
it records that CB-36 — the card written to teach *"a rule expressed as an enumeration gets fixed at
the sites someone enumerated"* — is closed with a tally that missed `import_csv`, because its
read-modify-write is a run-id sequence rather than a meta merge and did not look like what the
sweeper was reading for. The lesson landed on its own card.

## 2026-08-18 — iteration 2: nothing shipped, two cards advanced, stop condition reached

**Focus:** `codebugs`, pure bugfixes, simple first. **Disposition:** no card landed. **Stop
condition:** every remaining candidate under this focus needs a decision or is a refactor sprint.

**Blocked at Phase 0 by a live parallel session, and the right move was to do nothing.** Main had
five files STAGED and uncommitted — including a new 617-line `src/codebugs/grouping.py` — modified
between 22:28 and 22:35 with no branch and no worktree. Not stale dirt: another session mid-flight,
writing into main's checkout. Nothing was touched, stashed or committed. It resolved itself ~20
minutes later: that work landed properly as `feature/grouping-axes-read-surface` and
`fix/citation-count-units` (main d748a03 → 557d4aa), so the staged state was a moment in its
workflow, not a violation to clean up. **Waiting was the whole intervention.**

**A probe worktree inherits the repo's tracker, and that nearly wrote to it.** Diagnosing CB-86 in a
detached worktree under the scratchpad, `codebugs stats` returned the REAL tracker's 89 findings:
`db.connect()` follows a worktree's `.git` FILE to the main repo and resolves to its `.codebugs`
— documented behaviour, and exactly what makes a git worktree the wrong place for a CLI probe. An
`add` in the same chain did not run only because the `&&` chain had already broken on a failed
`init`. Verified no stray row landed. **Probe from a plain temp dir outside any worktree.**

**CB-86 upgraded from unreproduced to CONFIRMED, then handed back.** Two shapes, both raw
tracebacks: a read-only tracker DIRECTORY raises `sqlite3.OperationalError: attempt to write a
readonly database` at `db.py:1096` (the WAL pragma, during connection setup — pre-write), and a
read-only DATABASE FILE raises the same class later, from a write statement. Both escape because
`cli.main` catches `OperationalError` and re-raises non-contention. A third case checked and found
NOT defective: `--tracker-root <missing> init` under an unwritable parent already prints a clean
refusal. The card's decision is untouched by the reproduction, and the reproduction adds one
consideration to it: the two shapes raise in different phases, so a central arm classifies a
pre-write and a mid-write failure through one path — safe for "readonly database", not obviously
safe for a mid-transaction disk error.

**CB-68's premise is wrong, and measuring it was worth more than fixing it.** The card says
`blocker_counts_for` is a lone violation whose "sibling `deferred_id_restriction` follows the rule".
An AST sweep of every public `conn`-first function says: 37 total, **nine** with 2+ positional args
after `conn` (the identical defect), twelve following the de-facto `f(conn, entity_id, *, ...)`
pattern, sixteen with one positional and no keyword-only args at all. `deferred_id_restriction` is
in that last group — it does not follow the rule, it just has fewer arguments. **Under CLAUDE.md's
literal rule (`def f(conn, *, name, ...)`) all 37 violate it, including the ones the codebase treats
as exemplary.** So the card is not a three-line fix, it is the question *which rule is true* — amend
the doc to the practiced convention, or change 37 signatures (breaking, and a refactor sprint).
Fixing only `blocker_counts_for` was refused: it is one cell of nine, it would make the rule look
upheld, and nine independently landable edits blow the batching ceiling of four.

**Net change: 0 closed, 0 filed. Open stayed at 35** (CB-81 closed and CB-87 filed in the previous
iteration). Two cards carry materially better evidence than they did, and both hand-backs name a
decision precisely enough to answer in one line.

## 2026-08-18 — iteration 3: CB-88 + CB-89 fixed in one tree, merge 6dba444

**Focus:** `codebugs`, "stabilization batch related, in one batch". No `stabilization` tag, label or
plan exists anywhere in the repo or tracker, so it was read as *the defect-shaped robustness cards,
excluding the RFC and needs-decision ones* — mapping stated before ranking, per the skill.
**Disposition:** both cards fixed and landed. **Merge:** `6dba444`.

**Picked CB-88 (high) + CB-89 (medium); excluded CB-51 (the other high)** because "CSV import needs
a domain-level restore path" is a capability rewrite with an open one-transaction question — the
"needs a product decision" predicate, named rather than silently skipped.

**The clustering predicate did NOT hold, and the plan says so.** `bug-clustering.md` predicate 2
requires one transformation; these are two, in the same prologue of one function. Predicates 1 and 4
fail too. They shared a tree by **explicit user instruction** ("in one batch"), which outranks skill
policy — recorded as a scheduling decision, not a predicate that was talked into passing.

**One question asked, and it was the right one to ask.** CB-89's own suggested direction — resolve
the repo that owns the file and answer there — is a capability, not a bugfix: it makes the tracker
shell into a directory named by card data and redefines `reported_at_commit` per card. Presented
with both costs; the user ratified **honest scoping only**. The capability is CB-91.

**THREE DRAFTS. Adversarial review x2 returned FAIL twice, and the second FAIL was found by both
models independently.** Round 1: four reproduced FATALs. Round 2 killed the replacement design the
same way — **git canonicalizes the name it prints** (`./a/b`, `a//b` and an absolute path all come
back as `a/b`), so matching git's output against the caller's spelling regressed valid `current`
files to `unknown`. The fix that survived is to stop comparing spellings: **derive git's spelling
from the physical candidate path — the same one `os.stat` already resolves** — so the coordinate
systems agree by construction rather than by coincidence.

**The most valuable single finding was a population count.** CB-88's own census said 47
directory-valued cards. Measured on the motivating tracker: **155 findings across 51 distinct paths
(71 live) spell the directory with a TRAILING SLASH** — `dashboard-ui/` (48), `src/autosorter/` (13).
`git ls-tree -- 'pkg/'` returns the directory's *children* and no tree record at all, so drafts 1
and 2 would have repaired `src` and left `src/` reporting `deleted`: a fix for the minority of its
own population, shipped as a fix for the defect. **A card's census is evidence, not a boundary.**

**Two defects were fixed that were on neither card**, both found by running the code rather than
reading it: a free-text `file` returned **`current`** (a confident "unchanged since" about a path
that does not exist), and `file = ":"` made `git log` match the **whole history**, because the raw
value was passed as a pathspec.

**`/simplify`'s altitude pass caught the fix reproducing its own card's bug.** The scope check ran
before `cat-file` for an absolute value and after it for a relative one — so `../sibling/src/x.py`,
the natural spelling for a cross-repo card filed from a subdirectory, still reported
`unreachable_commit`. CB-89's exact defect, surviving inside CB-89's fix, because every test used an
absolute path. Scope is now decided first for every spelling and the pinned "git missing →
unreachable_commit" contract *falls out* instead of being special-cased. **A fix keyed on argument
spelling is an enumeration wearing a control-flow hat.**

**A test that would have gone vacuous was caught before it did.** `tests/test_provenance.py:402`
injects a failure at the *third* `subprocess.check_output` to pin CB-79's rename guard. The new
`ls-tree` probe moves which call is third — same assertion, same reason string, different branch.
Re-keyed on argv. Found by the Opus adversary, not by the suite.

**Measured outcome (autosorter tracker, 1215 live findings, 1063 distinct pairs):**
`deleted` **51 → 0** — every one of the 51 verified present on disk, so the bucket documented as
"the strongest deterministic dismissal signal available" was 100% false positive;
`current` 300 → 234 (66 were false freshness claims); `modified` 696 → 746;
`git_error`+`unreachable_commit` 7 → 0, now `out_of_repo` — exactly CB-89's population.
**27.1s → 14.0s**: the review predicted a 35% subprocess *increase* from call counting, and timing
it showed the opposite, because the false-`deleted` paths had been paying for a whole-repo rename
detection. **A cost claim derived from counting is a prediction until something is timed.**

**Net change: 2 closed, 3 filed by this iteration (CB-91 ratified deferral, CB-92 and CB-93 both
cross-model corroborated), 2 filed by a parallel session (CB-90, CB-94). Open 37 → 40.** The queue
grew, and it is the review machinery working: CB-92 and CB-93 are defects that were *found by
attacking this fix*, and CB-91 is a scope decision recorded rather than silently taken.

**Harness note:** `/fewer-permission-prompts` wrote `.claude/settings.json`, which is untracked on a
main that refuses every non-plan commit — it would have blocked *every* future `worktree-finish`.
Folded into the gitignored `.claude/settings.local.json` (89 unique entries) instead. The skill's
letter says project settings; its intent is fewer prompts, and only one of those survives contact
with this repo's pre-commit hook.

---

## Iteration 4 — 2026-08-18 · focus `codebugs` ("stabilization batch, one batch")

**Cards:** CB-92 + CB-93 → **both fixed**. Merge `f0b4010`
(branch `fix/cb-92-cb-93-one-coordinate-system`, commits `1a7dcb8`, `3e63cbc`, `5fb1f33`,
`3e101f0`). Filed: CB-95, CB-96. Deferred by user decision: CB-51.

**No "stabilization batch" existed in the tracker** — the focus was the user's framing, not a
named entity, so the batch was FORMED on evidence. The cluster predicate that justified one
tree was *same root cause*, and the measurement that sealed it: `git diff --name-status`
prints ROOT-relative paths regardless of cwd, so CB-92's rename comparison against a raw
cwd-relative spelling **is** CB-93's coordinate mismatch surfacing one screen down. CB-88
(iteration 3) had introduced the canonical `rel` and used it in exactly one probe; this
iteration finished the job.

**One question asked, and it was the right one.** CB-93 was tagged `needs-decision` and
genuinely was one: honour the documented root-relative contract, or redocument reality.
Both costs were measured before asking — the affected caller population is **0 of 3307
findings** across two real trackers, and the real cost is that `tests/test_provenance.py:901`
*deliberately pins* the cwd-relative reading. The user ratified **(a)**, so that pin was
rewritten as a declared contract change with a docstring saying so.

**THE CARD UNDERSTATED ITSELF: 2 routes named, 5 closed.** Canonicalization (`./src/x.py`),
subdirectory cwd, and **ambient locale** were all additional doors to the same confident
false `deleted`. The locale one is the sharpest: `text=True` decodes with
`locale.getpreferredencoding()`, so under `LC_CTYPE=C` git's UTF-8 path bytes arrive as
`src/\udcc3\udca4.py` and never match — **CB-92 arriving through a locale instead of through
quoting**, in a fix written for CB-92.

**THREE DEFECTS WERE INTRODUCED BY THE FIX AND CAUGHT BEFORE LANDING.** (1) FATAL: `-z`
suppresses the C-quoting that had been keeping output ASCII, so raw bytes hit `text=True`
and raised `UnicodeDecodeError` — a `ValueError`, outside the module's
`(SubprocessError, OSError)` contract; the probe takes no pathspec, so ONE undecodable
rename anywhere would have killed an entire `staleness_check` batch. (2) A surrogate in
`reason` crashed pydantic's `dump_json`, i.e. the MCP serializer — the fix answering
correctly and dying on the way out. (3) The rename parser failed OPEN into `deleted` on a
desynchronized record. Each is the module's signature defect reproduced inside its own fix.

**THE MOST UNCOMFORTABLE FINDING WAS ABOUT MY OWN EVIDENCE.** The round-1 sibling sweep
declared `db.git_rev_parse` safe **on a fixture that structurally could not fail** — it
varied a path *inside* the repo, while `--show-toplevel` only ever prints the root's *own*
name. A repo whose root directory name is non-UTF-8 crashed the whole batch. Separately,
several CB-92 tests were **ambient-git-config dependent**: with `core.quotePath=false` in a
developer's global config they pass against the UNFIXED code, and a local mutation check
would still "prove" they discriminate. The fixture now pins git's defaults, verified by
re-running against unfixed code under a hostile `HOME`. **A mutation check only proves a
test discriminates in the environment you ran it in.**

**Cross-model review: three attempts, one success, and the two failures were mechanical.**
`codex exec` hangs on stdin without `< /dev/null`, and a backgrounded relay reports before
Codex finishes. Rounds 1 and 2 therefore had a single-model attacker despite the standing
rule, which was stated at the time rather than papered over. Codex's eventual review
returned FAIL with two live findings (locale decoding, relative `GIT_WORK_TREE`), both
reproduced here before being believed, both fixed. **Its remaining finding was deliberately
NOT fixed** — macOS NFD/NFC normalization, which cannot be reproduced or verified on this
platform, so it is CB-96 rather than a blind guess.

**Net change: 2 closed, 2 filed by this iteration (CB-95 user request, CB-96 ratified
deferral). Open 40 → 40.** Flat, and that is the review machinery working rather than a
stall: CB-96 is a defect found by attacking this fix, and CB-95 is a capability the user
asked about mid-iteration, filed instead of absorbed into a two-edit stabilization tree.

**Deferred by user decision: CB-51 (the queue's only `high`).** Its correct fix is a domain
`findings.import_findings()`, and at least two of its four defects need semantic decisions
first — is importing an export a RESTORE (preserve id/status/occurrence_count) or an
OBSERVATION, and what happens when a foreign tracker's CB-1..CB-N collide with local ids.
Entangled with CB-77. **Next iteration opens with that design round, not with code.**

---

## Iteration 5 — 2026-08-18 · focus `codebugs` · **CB-51, the queue's only `high`**

**Cards:** CB-51 + CB-77 → **both fixed** (CB-51's `restore` half split out). Merge `461d8c8`
(branch `feature/cb-51-cb-77-import-restore-verbs`, commits `f78ec74`, `edbcb25`, `ae8d83a`).
Filed: **CB-97**. Open 40 → 40.

**The iteration opened with a DESIGN ROUND, not code** — the user's ratified sequencing from
iteration 4. Three decisions taken: two verbs (`import` = observation, `restore` = verbatim), a
colliding row must LAND with its origin recorded, and one transaction all-or-nothing (which also
settled CB-77, blocked on exactly that question). Grounding the questions in BOTH sides of the
round-trip is what made them answerable: `export-csv` writes `id, status, occurrence_count,
created_at, tags` and `_cmd_import_csv` read **none** of them.

**THE CARD UNDERSTATED ITSELF AGAIN — a fifth defect, on no card, was the most dangerous.**
`export-csv` orders by **severity**, so a normal backup is not in ascending id order; the allocator
then mints ids that later rows of the same file still name, and the guard rejects the CSV's real
`CB-2` because a freshly minted `CB-2` exists. **3 rows out, 2 back, exit 0** — silent data loss on
the disaster-recovery path, reported as success.

**A near-miss on evidence, caught before it reached the plan.** The first reproducer passed
`--file` where the parser takes a positional, so all three imports died on argparse and three
"reproductions" were about to be recorded from commands that never ran. The reproducer now raises
if the CLI exits on a usage error. Second iteration running in which the *evidence* was the thing
that needed checking, not the code.

**ADVERSARIAL REVIEW RETURNED FAIL AND KILLED THE DESIGN — the most valuable review yet.** Three
FATALs, all reproduced: `restore` cannot use the add path at all (`auto:` fingerprints refused,
reserved meta keys refused, status/occurrence_count/created_at not insertable); insert-as-`open`-
then-UPDATE **refuses a legitimate export**, because a `wont_fix` card and its `recurrence_of` twin
share a fingerprint by design — *my own answer to defect 3 was the input that broke my own design*;
and post-add hooks fire on the INSERT, so an N-row restore fabricates N triage items and 2N audit
rows asserting a history that never happened. It also killed an invention of mine that was never
ratified — "import is idempotent" — by measuring that explicit-id rows store `fingerprint = NULL`,
so a fingerprint-only skip cannot see them and the id guard I proposed deleting is the only thing
covering that population.

**Response was to SPLIT, not to push through.** `restore` became **CB-97** carrying every
constraint plus three fidelity gaps review surfaced (the export omits `reported_at_commit` /
`reported_at_ref`, is capped at `limit=100000`, and carries no milestone projections). The user's
decision is unchanged; only its delivery is sequenced. This tree shipped the half that is correct
today — three data-loss defects, the unfiled fourth, and CB-77.

**`/simplify` then found the seam had not fully moved.** The handler's docstring claimed "no import
semantics here" while still computing the reserved-meta strip, so the domain validated meta only
one caller had sanitized. And the row contract was **double** — the handler pre-decoded `meta` to a
dict while every new test passed the raw JSON string, which `dict()` cannot take; it only worked
because the fixtures export an empty meta column, so **the tests could not reach the contradiction
they contained**. Also: `skipped` counted two different facts and printed a reopen-skip as "already
present", which is the CB-15/CB-16 mislabel in the one line an operator reads.

**Cross-model: Codex failed a FOURTH time, and the cause is now known and recorded** — the Bash
tool caps `timeout` at 600000 ms regardless of what is requested, so a foreground `codex exec` on a
long review is killed at 10 minutes. Its eventual run reviews a plan this iteration has already
superseded. Iteration 4's Codex round did land and was valuable; this one did not, and saying so
beats implying a cross-model check happened.

**Net: 2 closed, 1 filed (CB-97). Open 40 → 40, but the queue's only `high` is gone** and what
replaced it is a medium with a fully specified design.

**Next:** CB-97 (`restore`) is ready to implement — its constraints are recorded, and it needs no
new user decision. Otherwise the queue is 21 medium / 18 low.

---

## Iteration 6 — 2026-08-18 · focus `codebugs` · **CB-97, the `restore` half of CB-51**

**Cards:** CB-97 → **fixed**. Merge `c8b739f` (branch `feature/cb-97-restore-verb`, commits
`ddc30f1`, `47af284`, `191596d`). Nothing filed. Open 39 → 38.

**Picked because iteration 5 left it implementation-ready** — the design constraints were all
established and reproduced during the review that split it out, so this iteration opened with
code rather than another design round. That is the payoff of splitting instead of pushing
through: the hard thinking had already been done and written down on the card.

**`restore-csv` writes an export back verbatim** — a raw multi-row INSERT in one `db.txn`
across all sixteen stored columns, bypassing the identity function, the pre-add resolvers and
the post-add hooks, refusing rather than merging. Verified on main: export → restore is
byte-verbatim, **including the `wont_fix` + `recurrence_of` pair that shares a fingerprint** —
the input that killed the previous design. Forcing statuses to `open` makes that test fail with
`IntegrityError` on `ux_findings_fingerprint_live`, so the test **reproduces the reviewer's
FATAL** rather than asserting around it.

**THREE DEFECTS I INTRODUCED IN THIS ITERATION, all caught before landing.** Recording them
because each is a shape this repo keeps meeting, and because two were found by the process
rather than by me:

1. **Adding two columns to the export HEADER while the positional row writer kept the old
   order** shifted every value after `meta` — the exported `fingerprint` became a timestamp.
   Caught by an existing cross-tracker round-trip test. Header and rows now derive from one
   `_RESTORE_COLUMNS` declaration; `tests/test_fsio.py` held a **third** copy of that list.
2. **My first de-capping fix used OFFSET paging**, and review showed that is unstable here:
   `query_findings` orders by severity rank and whole-second `created_at` with no unique
   tiebreaker, so a tie group straddling a page boundary can be emitted twice or skipped.
   **Duplicated or missing rows in a backup is strictly worse than the cap I was removing.**
3. That rewrite **deleted `conn.close()`**, leaking the connection and breaking the exact
   premise `TestExportPayloadIsInHandBeforeTheOpen` documents.

**A scale ceiling that would have made large backups unrestorable.** The collision pre-check
used one SQL placeholder per row, and `SQLITE_LIMIT_VARIABLE_NUMBER` is 32766 here (999 on
pre-3.32 builds) — measured, 40000 placeholders raise `too many SQL variables`. A tracker
could export a file it was unable to read back: this card's own defect, at scale. One bound
parameter via `json_each` now; chunking worked and was rejected as a magic number to get wrong.

**TWO TESTS WERE THEMSELVES WRONG AND WERE FIXED, NOT DELETED.** The export test shelled out
while monkeypatching an in-process constant, so it proved nothing — it now runs in-process and
pins the PROPERTY (the fetch asks for at least the row count; no OFFSET walk) rather than a row
count any cap size would satisfy. And `TestExportPayloadIsInHandBeforeTheOpen` asserted
`order[:2] == [produce, open]`, pinning the producer's CALL COUNT alongside the ordering it
actually cares about, so it went red on a change it has no opinion about — **a premise pin that
fails on something it does not care about teaches people to edit premise pins.** Both
re-verified by mutation.

**A verification claim was RETRACTED mid-iteration.** One mutation check's patch silently failed
to apply, so its "passed" result proved nothing; it was re-run correctly (and the pin did fail
under the real mutation). Third iteration in a row where the *evidence* needed checking, not
just the code.

**Harness note:** a `pkill -f "codex exec"` was refused by the CB-2680 guard, correctly — the
stale Codex run's PID was never captured, and pattern-killing has previously destroyed a
parallel session's work. Left running rather than worked around.

**Net: 1 closed, 0 filed. Open 39 → 38** — the first iteration this session where the queue
actually shrank, because the design cost was paid in iteration 5.

**Next:** queue is medium/low only. Candidates worth ranking: CB-92-family is closed; CB-63
(meta-batch, needs-decision), CB-37 (what enforces the CB-24 transaction rule — the obvious AST
predicate certifies the bug it was built to catch), CB-21 (update-surface parity test).

## Iteration 7 — 2026-08-19 · focus `codebugs` ("stabilization batch, one batch") · **CB-78, merge `88bfefe`**

**Disposition: CB-78 fixed. CB-55 handed back to `open` with better evidence. CB-86's design
ratified onto its card. CB-99 filed. Open 38 → 37.**

**The headline is that the batch the user asked for did not survive review, and splitting it was
forced by the rules rather than chosen.** The iteration opened with a genuine three-card cluster —
CB-78 + CB-86 + CB-55, "the CLI's I/O error boundary" — and `/adversarial-review-x2` (Opus adversary
+ Codex/GPT-5.6-Sol in parallel, then an Opus defender over the union) killed it before a line of
code was written. Both attackers reached the same verdict independently:

- **The predicate never held.** The plan claimed predicate 1 of `bug-clustering.md` (*one causal
  code change makes every reproduced symptom disappear*) while its own edit table listed **three**
  causal changes, one per card. The table was the disproof printed directly beneath the claim.
- **The "falsifiable evidence" self-destructed.** It justified the cluster with a contradiction
  between two `except` arms, then stated two sentences later that the shipped CB-78 fix is a signal
  disposition and adds no arm. A contradiction belonging to a *rejected* design cannot bind a tree.
- **The edit count was over the hard ceiling of four** before anything else: CB-55's six call sites
  are **three distinct transformations**, so even the most generous arithmetic gives 1 + 1 + 3 = 5.

**Three of my `file:line` citations were fabricated, and I only caught them because the review
did.** `findings.py:2216` is a dict field, not the `add` print (that is `:2144`); `db.py:1096` is a
`try:`, not the WAL pragma (`:1111`) — I copied that number from CB-86's card without opening the
file. Two stated premises were also simply false: "child processes stop inheriting `SIG_IGN`"
(`subprocess` already restores it — `restore_signals=True` is the default, measured both ways) and
"sqlite3 exception attributes are read-only" (they are assignable, and the test I cited as the
precedent for a stub *assigns onto a real exception*). **The lesson is not "cite carefully" — it is
that a number copied from a card is not a citation.**

**A user decision was re-opened because measurement inverted its headline example.** The SIGPIPE/141
semantics were ratified, then re-ratified after I measured that `export-csv /dev/stdout | gzip` —
the very scenario I had argued from — already exits 1 with a clean `codebugs: [Errno 32] Broken
pipe`, because `fsio.atomic_write` writes that path in place and CB-76's arm catches it. There was
no exit-0 path there to protect, and 141 **removes** a working diagnostic on that one command. The
ranking of the three original options was unchanged, so this was reported as a cost and re-ratified
rather than re-litigated. (My example was also mis-spelled: `export-csv -` writes a file named `-`.)

**Two design facts that only measurement could have produced, both from the defender:**
1. **The "polite" variant defeats the fix.** Installing `SIG_DFL` inside `main()` and restoring it
   in a `finally` — the obvious way to avoid mutating pytest's process state — puts the
   block-buffered case back to **exit 120**, because that write is the interpreter's shutdown flush
   and happens after `main()` returns. "No process-global mutation" and "fix the block-buffered
   case" are incompatible inside one function; the wrapper split is the only shape satisfying both.
2. **The leak was real, not theoretical.** Three test modules call `cli.main()` in-process, and a
   two-test file reproduced `pytest -q -s . | head -2` dying at 141 mid-suite.

**A defect I introduced inside the review's own fix, caught by mutation.** The altitude pass
replaced a source-TEXT ratchet with an AST one (CLAUDE.md already ratified AST for the sibling
`TestWriteCallSitesRatchet`). My first AST draft keyed on the callee's **module** being named
`signal`, so `import signal as _s; _s.signal(_s.SIGPIPE, _s.SIG_DFL)` injected into `server.py` left
it **green**. Now keyed on the callee's final name and verified red against three spellings, with the
residual `getattr` evasion stated rather than implied. **Third iteration running where the evidence
needed checking, not just the code.**

**I also hit the `git checkout -- <file>` trap this repo documents** — the mutation loop's cleanup
reverted an *uncommitted* `server.py` docstring I had added after the commit. Caught by reading
`git status` rather than by any gate. "Commit first, mutate second" has to cover edits made *between*
commits, not just the ones in the last commit.

**Sibling sweep:** exactly two process entry points exist; `codebugs-mcp` → `server.main` is a
deliberate NON-instance (its stdout is the JSON-RPC transport) and now says so at the call site
rather than only in a plan.

**Net: 1 closed (CB-78), 1 filed (CB-99). Open 38 → 37.** The filing is the review machinery
working, not noise: CB-99 is a `except sqlite3.Error: skipped += 1` in `reqs.import_markdown` that
turns a full disk into "Imported 0, skipped 47" at **exit 0** — found by the Codex attacker, and
filed separately only after the defender *measured its claimed CB-86 bypass to be false*.

**Next:** `fix/cb-86-tracker-unwritable`, whose design is fully ratified and written on the card —
a typed `db.TrackerUnwritableError` raised inside `db._open`, never a central
`sqlite3.OperationalError` arm at `cli.main` (the boundary option was refuted by this repo's own
`tests/test_bench.py:789`, which ratifies the traceback as the post-commit discriminator). Then
CB-55, which now carries its three-transformation breakdown and a `cliio.py` host recommendation.

## Iteration 8 — 2026-08-19 · focus `codebugs` · **CB-86, merge `c05250a`**

**Disposition: CB-86 fixed, FULL, with three limits named on the card. CB-100 filed. Open 37 → 38.**

**The design had already been ratified in iteration 7's review, so this iteration opened with a
reproducer instead of a design round** — the same payoff iteration 6 recorded. All four shapes were
re-reproduced by hand before any edit, in a plain temp dir outside any worktree, and two of the
card's own claims turned out wrong: every line number on it was stale, and there are **three** raise
sites inside `_open`, not two. The one that mattered was shape B — a read-only database file raises
from `merge.ensure_schema`, several frames down through `_open`'s `ensure_fn` loop — because that is
what makes ONE classification point sufficient for all four shapes.

**A design question the review had not covered, found only by implementing.** `SQLITE_CANTOPEN` (14)
is returned for BOTH "the file is not there" and "the file is there and I may not open it", with an
identical message — measured. So the error code alone cannot choose between "run `codebugs init`"
and "check your permissions", and classifying 14 as environmental unconditionally would have
regressed the main CB-23 path into telling people with a genuinely missing tracker that their
permissions are wrong. `os.path.exists` picks the message, which is exactly what `_open`'s own
docstring already says the resolver's `isfile` check is for.

**I shipped the rejected design's own door and the altitude reviewer found it.** `is_environmental`
was exported PUBLIC — so re-introducing the refused boundary arm became a two-line patch inside an
`except sqlite3.OperationalError` that already exists in `cli.py`, looking like an obvious tidy-up
while silently deleting the discriminator `tests/test_bench.py:789` protects. **A rule whose whole
content is "do not call this from there" cannot be enforced by exporting the thing.** Now
`_is_environmental` plus `TestTheClassifierStaysInsideDb`, and both halves are mutation-verified:
injecting that exact two-line patch turns it red, and renaming the function back to public turns its
companion red.

**The enumeration question got a better answer than "we were careful".** This is another allowlist
of codes, and this repo has been bitten by enumerations six times — but the reviewer supplied the
distinction worth keeping: in every prior case an unlisted member produced a *success-shaped wrong
answer*; here `if is_contention(e) or not _is_environmental(e): raise` means an unlisted code
produces the pre-CB-86 traceback. **An incomplete list fails toward the status quo, not toward a
lie**, and contention is tested first, so even a mistaken future addition of 5/6 cannot swallow a
retryable error. Two entries were kept OUT by measurement rather than reasoning: `SQLITE_PERM` (3)
never occurs (`chmod 000` yields 14) and `SQLITE_NOTADB` (26) arrives as `DatabaseError` and could
never reach the arm.

**Process notes worth carrying.** One test was corrected mid-iteration for *looking* discriminating
for the wrong reason — it asserted on the new helper, so it went red on main via `AttributeError`
rather than on behaviour; the assertion was dropped and its class now states plainly that it pins
preserved behaviour. And the efficiency review lane was skipped deliberately, with the number rather
than a shrug: the new code runs only inside an `except`, and the suite went 70.40s → 71.66s for 19
new tests, measured before skipping.

**Net: 1 closed (CB-86), 1 filed (CB-100). Open 37 → 38** — and the arithmetic is worth stating
because I got it wrong in the first draft of this row: closing CB-86 did NOT decrease the open
count, since the card was already `in_progress` and therefore not counted as open. Only the new
filing moved the number. Across iterations 7 and 8 together the queue is flat at 38: two closed,
two filed, one (CB-55) returned to `open` with better evidence than it had.** CB-100 is `describe_root` reporting a
`chmod 000` tracker as healthy while every verb now refuses it — **pre-existing**, made louder by
this change, and deferred out of this tree on the reviewer's explicit recommendation rather than
folded in.

**Next:** CB-55, which now carries its three-transformation breakdown, the measured six-copy count,
the `cliio.py` host recommendation, and a note that CB-78 left two of its six arms unreachable for
pipe destinations. Then CB-99 (the `reqs-import` swallow), which should reuse CB-86's classifier
rather than growing a second copy of the same enumeration.

## Iteration 9 — 2026-08-19 · focus `codebugs` · **CB-99, merge `03a12a3`**

**Disposition: CB-99 fixed, and reproduced in the process — it was filed NOT REPRODUCED. Open 38 → 37.**

**Candidate swapped, and the reasoning is the reusable part.** The plan after iteration 8 was CB-55.
CB-99 was taken instead because landing CB-86 had *unblocked* it, it is `medium` against CB-55's
`low`, it is a success-shaped lie (this repo's worst category), and it is ONE transformation plus a
sweep against CB-55's three transformations and two open decisions. **Ranking by what a card costs to
land, not by the order they were filed in.**

**The card's own prescribed fix was improved on, not followed.** It said to reuse CB-86's classifier.
That would have meant exporting `db._is_environmental` — private precisely so a CLI-boundary caller
cannot exist — or growing a second copy of its enumeration. The exception TREE already draws the
line: `IntegrityError` is the class for a row that violates the table's constraints, and every
environmental code routes to `OperationalError`. **No classifier, no second enumeration, and CB-86's
ratchet stays intact.**

**The sweep found a population of ONE, which is worth recording precisely because it is unusual
here.** `grep -rn "except sqlite3\." src/` gives eleven sites and exactly one is this shape; review
additionally swept the spelling grep cannot see (`except Exception`, bare) and found eight, all
ratified hook-swallow or cleanup shapes. This repo's standing answer is "the population is larger
than the list" — this time it genuinely was not, and saying so is as useful as the usual finding.

**THREE DEFECTS IN MY OWN WORK THIS ITERATION, and all three are the same family: a guard or a claim
that was true for the wrong reason.**

1. **A user-facing falsehood in the CHANGELOG.** I wrote that `skipped` is expected to stay 0. It has
   a SECOND, live producer sixty lines above — the `len(cells) < 4` guard — reachable by construction
   because `_ROW_RE` anchors only on the leading id cell. Measured: `{'imported': 1, 'skipped': 1}`.
   An operator meeting an ordinary nonzero `skipped` would have read my own documentation as "the fix
   regressed". Caught by the altitude reviewer, corrected in three places.
2. **A test true by construction.** `test_nothing_is_committed_when_it_escapes` failed on EVERY
   insert, so nothing was ever written and `landed == 0` held regardless of where the commit sat —
   which is the property its docstring claimed to pin. Now fires on the second row, and verified red
   when a per-row `conn.commit()` is injected.
3. **A ratchet that lost coverage while being made "better".** Iteration 8's classifier ratchet
   checked the NAME against source TEXT, so this card's comment explaining why it does NOT use that
   classifier tripped it — a **false refusal that punishes the documentation keeping the rule
   understood**, and CLAUDE.md already records that exact lesson for `TestWriteCallSitesRatchet`. I
   rewrote it to AST — and the rewrite then silently LOST what the text version caught: an aliased
   import and a `getattr` constant both went unseen. **Replacing a coarse check with a precise one
   can narrow what it catches, and the narrowing has to be measured rather than assumed.** Four node
   kinds now, verified red on the aliased import.

**The pattern across iterations 7, 8 and 9 is worth naming: three iterations running, the defect
review found was in my own guard rather than in the fix.** An evadable ratchet (alias), an exported
predicate that reopened the door it was built to close, and now a ratchet that both false-refused and
under-covered. The fixes themselves survived review each time.

**Net: 1 closed (CB-99), 0 filed. Open 38 → 37.**

**Next:** CB-55 remains, and it is genuinely the heaviest of the three original batch members —
three independent transformations, a host decision (`cliio.py` rather than `cli.py`, so the MCP
server does not import argparse), and a declared choice about whether the latent connection leak on
the non-`OSError` path is preserved or fixed inside a "pure refactor".

---

## Каскад DIR-2 (записи принимающего уровня, П-В/§8.2)

- **Т-3** — CB-60 fixed (fail-closed нормализация категории), мердж `fa40b3c`, принят DIR-2 2026-08-20 (7/7 §13, /acceptance-check). Открыто попутно: CB-61 разблокирована.
- **Т-5** — CB-53 fixed (staleness читает ring), мердж `fc4f126`, принят DIR-2 2026-08-19 (пп.1,4,5 + замысел).
- **Т-8** — CB-114+CB-115 fixed (докстринг импорта; индекс ref в _POST_MIGRATION_INDEXES — отклонение от буквы брифа, подтверждено), мердж `d3ff33f`, принят DIR-2 2026-08-20 (7/7, /acceptance-check).
- **Т-9** — строка `tags` контракта BT-4 (минимальный union C-ALT-1, promote_tags-ратчет, снятие — открытое суб-решение), мердж `d491432`, принят DIR-2 2026-08-20 (7/7, /acceptance-check). Карт нет; CB-103 у DIR-2.
- **Т-10** — строка `category` контракта BT-4 + CB-113(b): ring несёт category, гейт после ветки дедупа, мердж `0671881`, принят DIR-2 2026-08-20 (7/7, /acceptance-check). CB-113 у DIR-2 до пути (a).

## Каскад DIR-1 (записи принимающего уровня, П-В/§8.2)

Записи вносятся принимающим уровнем (2). Т-2 и Т-6 записаны задним числом 2026-08-20 — приняты
были в срок, строка леджера пропущена мной; отмечаю, а не подчищаю молча.

- **Т-2** — CB-106 fixed (`codemerge_abandon` объявлен MCP-тулом; докстринг `codemerge_finish`
  приведён к коду и к ратифицированному дизайну — предложение карты переписать семантику
  `success=False` отвергнуто по доказательству), мердж `f3cc0cd`, принят DIR-1 2026-08-19
  (пп.1,4,5 + замысел). Попутно: CB-110 и CB-111 заведены, CB-42 дополнена доказательством.
  Кросс-модельный гейт при посадке пропущен, закрыт ретро-проходом: улов 0.
- **Т-6** — CB-111 fixed (`abandon_session` возвращает строку, которую записал: `UPDATE …
  RETURNING *` внутри `db.txn`, оба `conn.commit()` убраны), мердж `c0dce5e`, принят DIR-1
  2026-08-20 (7/7 §13, /acceptance-check). Зафиксировано в карте: первый мутант исполнителя был
  ЭКВИВАЛЕНТНЫМ (зелёным по построению), он заметил сам и переставил.
- **Т-7** — CB-116 fixed (заголовок мержа выводится из first-parent-линии самой ветки, а не из
  хвоста, загрязнённого форвард-мерджем main; каждая подсказка на повтор эхует `--merge-msg`),
  мердж `250f887`, принят DIR-1 2026-08-20 (7/7 §13, /acceptance-check). Юнит вёлся двумя
  сессиями: первая зависла на `codex exec` и остановлена, вторая нашла в её работе регрессию
  (диапазона без `--first-parent` недостаточно) и закрыла её. Заведена CB-121.
- **Полоса A / BT-1:** разрез дважды возвращался с гейта `adversarial-review-x2` — 3/10, затем
  4/10. Популяция и предикаты переведены в исполняемые скрипты (`.claude/plans/bt1-scripts/`,
  мердж `5d48bef`), потому что три ручных вывода покрытия подряд оказались неверны.
- **Т-11** — декларации `source`/`reported_at_ref`/`meta` контракта BT-4 (docs-only, пины прозы, golden), мердж `0466d44` (посадку довёл DIR-2 после смерти менеджера в финише). Контракт BT-4 посажен целиком, 5/5 строк.
