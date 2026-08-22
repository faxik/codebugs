# BT-7 v3 — hostile adversarial review (Codex / GPT-5.6 Sol, round 3)

Target: `.claude/plans/BT-7-location-anchor.md` (v3, commit 88ee00a).
Model: gpt-5.6-sol, reasoning=high, sandbox=read-only, isolated CODEX_HOME (no MCP servers).
Dispatched by the DIR-2 holder, 2026-08-22. Verbatim output below.

## closure_audit

| round-2 finding | family | CLOSED / REWORDED-ONLY / STILL OPEN / NEW-DEFECT | evidence |
|---|---|---|---|
| Ring-first storage makes repair/retraction impossible | Opus F-1; Codex MR5 | CLOSED | v3 selects only top-level `meta.loc`; the reader has one source and `loc:null` is effective: `BT-7-location-anchor.md:182-207,226-260`. |
| Per-occurrence storage was justified by a zero-use population | Opus F-2 | CLOSED | v3 reproduces zero re-observations and abandons ring storage: `BT-7-location-anchor.md:194-207`. Live read still shows zero, now 0/135 and 0/3301. |
| Wrong tracker root used as worktree root | Opus F-3; Codex MR8 | REPLACED-BY-A-NEW-DEFECT | Resolution now names a worktree-root API and drops the broken surfaces: `BT-7-location-anchor.md:312-328`. Capture, however, has no `project_dir`/cwd/root carrier in `add_finding` or the resolver observation: `findings.py:1200-1216,1052-1075`. |
| Two pre-lock carriers, seventh registry and fourth opt-out | Opus S-1; Codex MR2 | REPLACED-BY-A-NEW-DEFECT | Existing resolver removes the old carrier. But per-batch path caching has no lifecycle or context in the resolver signature: `db.py:265-286`; `findings.py:1860-1878`; v3 claims it at `BT-7-location-anchor.md:273-277`. |
| Pre-lock capture invalidates structural identity ordering | Opus S-2; Codex MR3 | CLOSED | Capture moved behind fingerprint derivation: `findings.py:944-947,1052-1075`; `BT-7-location-anchor.md:216-224`. |
| `sites`/`site` routed to an unreadable grammar; adopted basename gate unmeasured | Opus S-3 | REPLACED-BY-A-NEW-DEFECT | Branches and yields now reproduce, but basename equality accepts contradictory directory paths; the grammar treats `src/a/server.py` and `src/b/server.py` as corroborating. `BT-7-location-anchor.md:394-410`. |
| C2 reused no hunk parser | Opus S-4; Codex DS6 | CLOSED | C2 is removed and the zero reuse is admitted: `BT-7-location-anchor.md:171-180`. |
| C2 maps from the wrong dirty-tree base / omits working-tree edits | Codex MR6–7 | CLOSED | Those incorrect mechanisms were deleted. The replacement trigger is itself uncomputable; see major risks. |
| `staleness_check(anchor=True)` produces two roots | Opus S-5 | CLOSED | The surface is deleted: `BT-7-location-anchor.md:312-320`. |
| `get(resolve_anchor=True)` requires core-extension coupling | Opus S-6 | CLOSED | The surface is deleted for exactly that reason: `BT-7-location-anchor.md:312-315`. |
| Failure vocabulary split across capture/read/status | Opus S-7; Codex DS8 | REWORDED-ONLY | One list and status/reason boundary now exist at `BT-7-location-anchor.md:443-454`, but no exception-to-token mechanism is specified, and `unreachable_commit` appears at line 478 outside the allegedly closed list. |
| Ring import semantics were total loss, not selective stripping | Opus S-8 | CLOSED | Top-level storage inherits the actual top-level strip/restore behavior: `BT-7-location-anchor.md:259-260`; `findings.py:1340-1359,1612-1621`. |
| Ring size and immortal first ten entries understated | Opus S-9; Codex DS3 | CLOSED | Ring storage was rejected and its real cost recorded: `BT-7-location-anchor.md:184-207`. |
| `context_before`/`context_after` ambiguous or uncapped | Opus W-1 | STILL OPEN | They are described as actual integer widths at `BT-7-location-anchor.md:232-235`, but the read-validation list at lines 248-253 omits type, sign, and `≤ CONTEXT_LINES` invariants for both. |
| All resource caps were names rather than numbers | Opus W-2; Codex MR9/MR8 | CLOSED | 1 MiB, five lines, three context lines, 24 characters and 2048 bytes are explicit: `BT-7-location-anchor.md:346-361,496-501`. |
| Incorrect third-digit claims, arithmetic gaps, undated corpus | Opus W-3/W-4/N-7 | CLOSED | v3 publishes a dated measurement record and corrects the counts: `BT-7-MEASUREMENTS-2026-08-22.md:22-43,369-388`. |
| Backfill had no chosen commit or unreachable behavior | Opus W-5; Codex MR7 | REPLACED-BY-A-NEW-DEFECT | Commit selection is specified at `BT-7-location-anchor.md:478`, but its new `unreachable_commit` result violates the closed reason vocabulary at lines 447-451. |
| Recapture had no concrete write path | Opus W-6; Codex MR9/MR12 | STILL OPEN | A verb is named at `BT-7-location-anchor.md:279,587-588`, but its transaction phase, failure replacement policy, root binding and tombstone override semantics remain absent. |
| Anchor payload deep-copied into unrelated resolvers | Opus W-7 | CLOSED | There is no precomputed anchor payload in the observation. The ordinary observation is still deep-copied per resolver at `db.py:401-438`. |
| Wrong/null root default | Opus W-8 | REPLACED-BY-A-NEW-DEFECT | Read-side `unknown(no_root)` is explicit, but add-side capture still lacks any root input. |
| Cost extrapolation was unmarked and ignored batch pool growth | Opus W-9; Codex DS7 | REPLACED-BY-A-NEW-DEFECT | Costs were remeasured, but the new probe measures only a subset of capture and its own numbers contradict the 25% gate: `BT-7-MEASUREMENTS-2026-08-22.md:1502-1527,1538-1549`; `BT-7-location-anchor.md:515-526`. |
| Malformed/spoofed manual anchors accepted on update | Codex MR10/MR7 | STILL OPEN | v3 explicitly declines ingress validation: `BT-7-location-anchor.md:281-294,593-598`; `findings.py:1954-1956,2020-2024` performs unrestricted merge after reservation exemption. |
| Multi-key grammar had no precedence | Codex MR11 | REPLACED-BY-A-NEW-DEFECT | Key precedence now exists at `BT-7-location-anchor.md:416-431`, but invalid-first/fallback-to-next behavior and `sites_dropped` treatment across redundant keys are unspecified. |
| Capture failures and multisite loss were not persisted | Codex MR4/MR12 | REWORDED-ONLY | Failure objects and counters are specified at `BT-7-location-anchor.md:238-246,433-451`; resolver exceptions still bypass them and become `resolver_errors`: `db.py:449-460`. |
| Promised MCP/CLI surfaces absent from the cut | Codex MR12/MR6 | STILL OPEN | T-b now mentions MCP and CLI, but outer response shape, pagination, missing-ID behavior, CLI formatting and exit semantics remain undefined: `BT-7-location-anchor.md:296-310,587-588`. |
| Storage comparison biased table and omitted `locs:[…]` | Codex DS1–2 | STILL OPEN | The table option is now fairly compared, but the bounded top-level multi-anchor object remains unevaluated while the document again calls a table the only multi-anchor answer: `BT-7-location-anchor.md:184-210`. |
| `MAX_TEXT_BYTES` used code points | Codex DS4 | CLOSED | v3 explicitly uses encoded UTF-8 bytes: `BT-7-location-anchor.md:252,359-361`. |
| Per-occurrence anchor not bound to occurrence file | Codex DS5 | CLOSED | Top-level `loc.path` stores the actually read path: `BT-7-location-anchor.md:226-235`. |
| CB-65 “consumer” claim unsupported | Codex DS7 | CLOSED | v3 weakens the assertion and disclaims an interface: `BT-7-location-anchor.md:484-490`. |
| Historical measurements lacked reproducible artifacts | Codex DS8 | STILL OPEN | Scripts are committed, but the CSVs existed only in scratch and no hashes were recorded: `BT-7-MEASUREMENTS-2026-08-22.md:421-424`. The live counts changed from 129/3300 to 135/3301 during this audit. |
| Safe-open and snapshot-integrity contract absent | Codex missing requirements | REWORDED-ONLY | Full-path `realpath`, `S_ISREG`, and a TOCTOU paragraph were added: `BT-7-location-anchor.md:496-530`. There is still no fd-bound containment or pre/post-`fstat` mechanism. |
| Resolution algorithm incomplete | Codex missing requirement | REPLACED-BY-A-NEW-DEFECT | v3 adds a global scan and context filtering, but prose and table disagree on nearest-candidate behavior and zero context survivors are undefined: `BT-7-location-anchor.md:363-383`. |
| Numeric limits and EOF behavior missing | Codex missing requirement | CLOSED | Limits and `out_of_range` are explicit: `BT-7-location-anchor.md:346-361,496-501`. |
| Scope over file moves/D5 was overstated | Codex MR scope findings | CLOSED | v3 scopes the claim to lookup within a card and `auto:v1`: `BT-7-location-anchor.md:31-38,216-224`. |
| Round-1 registrations and behavior-change slice incomplete | Judge fix 12; both families | CLOSED | T-a names three registrations and add/update/import behavior changes: `BT-7-location-anchor.md:577-588`. |
| Confirmation-only findings (`ast` absent, commit evidence, writer source) | Opus N-3/N-4/N-5 | CLOSED | No corrective action was required; v3 retains the verified premises. |

## major_risks

- **FATAL — The claimed anchorable population is wrong by more than 4×** | `BT-7-location-anchor.md:164-167,389-410,536-545`; measurement script `BT-7-MEASUREMENTS-2026-08-22.md:968-1044` | The claim “2907/3300 can be anchored” counts regular files but ignores that capture also requires a locational value. Reproduction: autosorter has 709 rows with any source key and only 677 with both a source key and a regular file—an upper bound of 20.5%, not 88%. Codebugs is at most 62/135. The owner-facing “~12% cannot be anchored” cost is therefore false; roughly four-fifths cannot even enter the grammar.

- **FATAL — Capture has no correct worktree-root carrier** | `findings.py:1200-1216,1273-1289,1052-1075`; `db.py:265-286`; `BT-7-location-anchor.md:262-266,322-328` | `add_finding` has no `project_dir`, and the resolver observation contains neither cwd nor root. The DB identifies the tracker, not the active linked worktree. Calling ambient `getcwd()` merely recreates the binding defect; deriving from the connection yields main’s checkout. Thus “zero signature changes” and “capture against the worktree root” cannot both be implemented.

- **FATAL — The cost measurement does not measure the proposed capture** | `BT-7-MEASUREMENTS-2026-08-22.md:1538-1549`; planned work at `BT-7-location-anchor.md:333-361,496-508`; real resolver overhead at `db.py:401-460` | `capture_once` measures only `open/read/decode/strip/two hashes`. It omits grammar, full-path `realpath`, containment, stat/fstat, node classification, NUL scan, exact normalizer, text-byte cap, root discovery, HEAD capture, savepoint, validation and deep copies. The public root precedent invokes `git rev-parse`: `provenance.py:59-72`; `db.py:582-607`. I measured that omitted warm call at median 1.264 ms, versus the claimed 0.192 ms entire capture.

- **FATAL — v3’s own arithmetic violates its 25% landing gate** | `BT-7-location-anchor.md:268-277,515-526`; `BT-7-MEASUREMENTS-2026-08-22.md:218-238` | The stated endpoints imply 1.2–46.6%, not 2–8%: `0.671/1.44 = 46.6%` at a shallow pool. The document’s own cold estimate adds 100 ms to a 125 ms batch—80%—and to a 303 ms batch—33%, both above its 25% gate. The 2–8% statement cherry-picks deep similarity pools.

- **FATAL — “cache by path per batch” is not implementable in the existing seam** | `db.py:265-286,379-438`; `findings.py:1860-1878`; `BT-7-location-anchor.md:273-277,515-519` | A resolver receives only `(conn, observation)` per member. It gets no batch object, cache, member index, start/end notification or cleanup hook. A module-global path cache is stale across batches and worktrees; a transaction-keyed cache has no reliable boundary callback. This reverses round-2 Codex’s two-phase recommendation: v3 removed the carrier but also removed the only natural place a batch-scoped cache could live.

- **FATAL — Any MCP add caller gains an in-repository file-read oracle** | `findings.py:2960-3001,1099`; `db.py:623-634`; `BT-7-location-anchor.md:226-235,536-545` | Containment protects only “inside worktree”, not “safe or source-controlled”. `.git/config` currently passes all stated boundary predicates: inside root, regular, 546 bytes. So do untracked `.env`, credentials and build artifacts. The captured normalized text is persisted in `meta.loc` and returned in the add result. Repeated distinct findings can extract a file in 1–5-line chunks. The owner question addresses only outside-worktree reads and misses this capability expansion.

- **FATAL — A regular file can still monopolize the write lock indefinitely** | `db.py:546-576,1255-1261`; `findings.py:1859-1878`; `BT-7-location-anchor.md:496-501` | `S_ISREG` excludes FIFO/devices but does not bound `open()` or `read()` latency on NFS, FUSE, stale mounts, automounters or regular pseudo-files. Python file I/O has no timeout here. The first writer holds `BEGIN IMMEDIATE`; every concurrent writer times out after five seconds, while a 100-member batch retains the lock across every path.

- **SERIOUS — Resolver exception semantics contradict persisted capture failures** | `db.py:407-460`; `BT-7-location-anchor.md:238-246,443-451` | The runner catches every ordinary exception, continues the add and writes `meta.resolver_errors`. Therefore every filesystem/parsing failure not explicitly caught inside `loc.py` produces no `loc` failure object, corrupting failure-rate and `sites_dropped` metrics. “Capture failure is never `resolver_errors`” is currently a sentence, not a closed exception-mapping mechanism.

- **SERIOUS — Full-path `realpath` does not close the symlink-chain race** | `BT-7-location-anchor.md:503-508,528-530`; current path precedent `provenance.py:75-95` | Containment is decided from a pathname before the planned open; `S_ISREG` is checked after opening. An intermediate or final symlink can change between those operations, so the opened fd can name an external object even though the earlier `realpath` was internal. `fstat` proves “regular”, not “contained”.

- **SERIOUS — Deleting C2 leaves an uncomputable trigger for rebuilding C2** | `BT-7-location-anchor.md:372-378,470-476` | A zero-candidate content search returns `lost` with `reason=null`. Yet C2 is triggered by the fraction of `lost` cases caused specifically by edits to anchor lines. A-only resolution cannot distinguish an edit from deletion, wrong historical coordinates, file replacement or capture corruption. Computing that trigger requires the history/diff mechanism that the trigger is supposed to decide whether to build.

- **SERIOUS — The normalizer can report semantically edited code as unchanged** | `BT-7-location-anchor.md:335-343`; reproduced against its formula | Four and seven leading spaces both normalize to depth 1; `return "a  b"` and `return "a b"` both normalize to `(1, 'return "a b"')`. Thus the claim that reindent or edits to anchor lines break the hash is false. Internal whitespace collapse alters string literals, regexes and comments, while `indent // 4` hides 1–3-column indentation changes.

- **SERIOUS — Resolution has two contradictory ambiguity algorithms** | `BT-7-location-anchor.md:363-383` | Prose says the nearest context survivor wins when uniquely nearest. The table says any `>1` survivors are `ambiguous`. It also defines no outcome when anchor-hash candidates exist but context filtering leaves zero. These branches change confident `moved` into `ambiguous` or `lost`.

- **SERIOUS — Update still permits a validly shaped forged anchor** | `db.py:348-359`; `findings.py:1954-1956,2020-2024`; `BT-7-location-anchor.md:281-294` | Read validation rejects malformed objects only. An updater can submit a structurally valid `loc` with chosen text, hashes, coordinates and commit, defeating the stated “caller-supplied coordinate is spoofing” rule. The sanctioned verb is convention, not enforcement.

- **SERIOUS — “Every insert” is false** | `BT-7-location-anchor.md:184-187,262-266`; `findings.py:1019-1028,1052-1075,1399-1405,1618-1621` | Resolvers fire only for `finding_id is None and annotate`. Explicit-ID inserts, imports and restores create rows without either a successful or skipped `loc`. The storage table and lost-rate reasoning must name the actual population: annotated auto-ID insert continuations only.

- **SERIOUS — Basename corroboration admits contradictory paths** | `BT-7-location-anchor.md:394-410`; regex method `BT-7-MEASUREMENTS-2026-08-22.md:979-1008` | The exact proposed test evaluates `basename(file) == basename(token)`. Reproduction: `src/a/server.py` and `src/b/server.py` pass. The current corpus happens not to contain a directory-disagreeing gate pass, but the contract permits one and would confidently apply the foreign line number to the authoritative file.

- **SERIOUS — Recapture is a named verb without failure semantics** | `BT-7-location-anchor.md:279,587-588` | It is unspecified whether recapture reads inside an update transaction, whether transient failure replaces a valid anchor with `skipped`, whether it may override `loc:null`, and whether it uses compare-and-swap against concurrent updates. These decisions determine whether the repair path can destroy the last good anchor.

## design_smells

- **The file-size calibration samples the wrong population** | `BT-7-MEASUREMENTS-2026-08-22.md:1616-1631` | It crawls generated/vendor trees but excludes `.jsonl`; the live tracker actually references a 25,178,665-byte `benchmarks/nightly/history.jsonl` that the calibration never sees. Current referenced-file rates are 4/2917 row-weighted or 2/935 unique paths over 1 MiB, versus the cited 10/59164 repository crawl.

- **`os.path.isfile` was mislabeled “anchorable”** | `BT-7-MEASUREMENTS-2026-08-22.md:500-516,194-208` | It follows symlinks and tests neither containment, grammar, size, NUL content, line range nor minimum anchor length. It can only establish “currently resolves to a regular file”.

- **Every-20th sampling is systematic, not random** | `BT-7-MEASUREMENTS-2026-08-22.md:1684-1724` | Always sampling phase zero can correlate with generated/template line periodicity. A million correlated observations do not justify confidence in two decimal places; all 20 offsets or randomized file-stratified sampling are required.

- **Timing evidence has no distribution** | `BT-7-MEASUREMENTS-2026-08-22.md:1502-1527`; acknowledged at lines 413-414 | Forty serial adds and one batch per pool give no variance, confidence interval or interleaved control. The landing gate compares future measurements against machine-specific historical absolutes.

- **The corpus is mutable and unauditable** | `BT-7-MEASUREMENTS-2026-08-22.md:22-43,421-424` | Only commands and scripts were committed. During this audit codebugs moved from 133 to 135 rows, while the document’s 129-row CSV is unavailable. Exact historical claims cannot be rerun.

- **`sites_dropped` conflates different losses** | `BT-7-location-anchor.md:350-352,433-441` | It counts unselected atomic places, but also records truncating one long range to five lines. “One other site” and “86 discarded lines from the chosen site” become the same value, weakening the table/multi-anchor trigger.

## missing_requirements

- **Capture binding contract** | `findings.py:1200-1216,1052-1075` | Specify how the exact active worktree root and capture-time HEAD enter the resolver without ambient cwd or tracker-root inference.

- **Bounded-lock contract and concurrency acceptance test** | `db.py:546-576,1259-1261`; `findings.py:1859-1878` | Define a maximum permissible in-lock filesystem phase and deterministically test slow open/read, a second writer’s outcome, batch rollback and `busy_timeout`.

- **Atomic safe-open mechanism** | `BT-7-location-anchor.md:503-508` | Bind containment to the opened fd—e.g. fd-relative no-follow traversal/openat2 where available, or open then verify the fd’s resolved object—and compare pre/post `fstat` for mutation/deletion.

- **Closed exception-to-reason table** | `db.py:449-460`; `BT-7-location-anchor.md:447-451` | Enumerate `ENOENT`, `ENOTDIR`, `EACCES`, `ELOOP`, short reads, growth past cap and decoding cases; test that none becomes `resolver_errors`.

- **Grammar fallback rules** | `BT-7-location-anchor.md:401-431` | Define invalid higher-priority key versus valid lower key, zero/descending ranges, partial out-of-range spans, duplicate sites, and exact `sites_dropped` counting.

- **Stored-object validation completeness** | `BT-7-location-anchor.md:248-253` | Validate `context_before/context_after` as non-bool integers in `[0,3]`, require the exact key set for success/skipped variants, and define how over-2048-byte normalized anchor text degrades.

- **Resolution/API completeness** | `BT-7-location-anchor.md:296-310,363-383,587-588` | Define zero context survivors, nearest ties, outer batch response, limits/pagination, missing IDs, CLI output and exit codes.

- **Recapture consistency policy** | `BT-7-location-anchor.md:279,587-588` | State whether failure preserves the previous anchor, how tombstones are overridden, and what concurrent row/file changes cause refusal.

- **Computable deferred-feature metrics** | `BT-7-location-anchor.md:470-480` | Replace the circular C2 trigger, include `unreachable_commit` in the closed vocabulary, and define denominators for lost/capture-failure/multisite rates.

## alternatives

- Restore a two-phase capture object, but make it an explicit immutable `CaptureContext` containing worktree root, capture-time HEAD and a per-batch path→snapshot cache. It must remain outside fingerprint inputs.

- Make capture an explicit post-add/recapture operation outside the write transaction. Insert first, then atomically update `meta.loc` with a row-version check; report “finding committed, anchor pending/failed” honestly.

- Store hashes and coordinates but omit normalized source text by default. Resolution does not require persisted text, and this removes the most direct file-exfiltration channel.

- Restrict automatic reads to tracked files, or at minimum deny `.git/`, `.codebugs/`, secret-like dotfiles and paths outside an explicit source allowlist. Make broader access an owner-approved capability.

- Use a top-level bounded `locs:[…]` object before introducing a table. It preserves same-file multisite data without the inaccessible occurrence ring; move to a table only when hash queries or unbounded history are actually required.

- Prefer structured `line`/`end`/`sites` API arguments for new callers and retain legacy meta grammar only as a measured compatibility adapter.

## measurement_reproduction

| number | claimed | my value | verdict |
|---|---:|---:|---|
| Re-observations | 0/129; 0/3300 | 0/135; 0/3301 | Property reproduced; historical denominators not reproducible because no CSV artifact exists. |
| Post-CB-43 re-observations | 0/87; 0/153 | 0/93; 0/154 | Zero reproduced; date cutoff is only a proxy for “used identity”. |
| Autosorter rows naming no filename | 609/709 | 609/709 | PASS, exact. |
| Filename-count distribution | `{0:609,1:47,2:23,3:17,4:6,5:7}` | same | PASS, exact. |
| Single-name agreement | codebugs 22/1; autosorter 41/6 | same | PASS, exact. |
| Basename gate, `lines` | codebugs 19/32; autosorter 18/52 | same | PASS, exact. |
| Basename gate, `sites` | codebugs 3/6; autosorter 7/32 | same | PASS, exact. |
| Basename gate, `site` | autosorter 15/15 | 15/15 | PASS, exact. |
| `lines` multi-place/multi-file | codebugs 24/12 of 44; autosorter 268/30 of 559 | same | PASS, exact. |
| `sites` multi-place/multi-file | codebugs claimed 11/3 of 18; autosorter 34/23 of 36 | current codebugs 12/3 of 19; autosorter exact | Live-data drift only. |
| File profile corpus | 59,161 | 59,164 | Historical total drifted by three files. |
| Combined byte p99 | 129,738 | 129,738 | PASS. |
| Combined max bytes | 5,051,718 | 5,051,718 | PASS. |
| Files over 1 MiB | 10/59,161 | 10/59,164 | Numerator exact; denominator drift. |
| Codebugs byte p50/p99/max | 15,024 / 163,471 / 188,333 | 15,056 / 168,934 / 191,158 | FAIL exact reproduction due repository growth. |
| Autosorter byte p50/p99/max | 4,945 / 129,529 / 5,051,718 | exact, with n=58,915 rather than 58,914 | Essentially reproduced. |
| “Anchorable” autosorter | 2907/3300 | ≤677/3301 | FAIL decisively: readable-file count omitted the grammar population. |
| Capture share of lock time | 2–8% | 1.2–46.6% from the document’s own endpoints | FAIL as a general claim. |
| Cold pessimism versus 25% batch gate | +100 ms | +80% at pool 0; +33% at pool 200 | FAIL at the two shallow pools. |
| Omitted root-discovery cost | not measured | warm `git rev-parse --show-toplevel`: median 1.264 ms, mean 1.456 ms, p95 2.804 ms over 100 runs | The claimed 0.192 ms is not end-to-end capture cost. |
| Actual referenced files over 1 MiB | not measured | autosorter 4/2917 row-weighted; 2/935 unique paths | The repository crawl understates the relevant rejection population. |

## confidence

0.97 — I could not execute the exporter or create symlink/NFS fixtures because the enforced filesystem sandbox is read-only; I instead queried both databases with immutable read-only SQLite connections. The historical 129/3300 CSVs were not preserved, so only stable live-data properties—not their exact old denominators—could be verified.