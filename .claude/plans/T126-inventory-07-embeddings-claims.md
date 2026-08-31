# Inventory: CLAUDE.md lines 953–1265 (Architecture migration / Embeddings / Claims module / Milestones module)

## Architecture migration (in progress)

L955 | RULE | The project is migrating toward a plugin architecture in phases, and the full plan (ARCH-001 through ARCH-005) is queried via `reqs_query --section "Architecture Migration"` or MCP tool `reqs_query(section="Architecture Migration")`.
L957 | MEASURED | All five migration phases are complete: schema registry (ARCH-001), tool registration (ARCH-002), entity types (ARCH-003), CLI unification (ARCH-004), embedding separation (ARCH-005).
L960 | RULE | New domain modules must call `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` at module level, and must NOT edit `db.connect()`, `server.py`, or `cli.py`.
L961 | RULE | The new module's import must be added to `_ensure_modules_loaded()` in `db.py` — described as temporary, until auto-discovery exists.
L962 | RULE | The new module's mode slug must be added to `SERVER_NAMES` (`server.py`) and to the `--mode` allowlist (`cli.py`) so the module can be loaded in isolation.
L963 | RULE | Self-contained modules that register themselves are preferred over central wiring.

## Embeddings

L967 | RULE | `embeddings.py` stores a vector per requirement and answers similarity queries over them.
L968 | RULE | There is no embedding provider inside this package, and every other rule in the section follows from that single fact.
L969 | RULE | The caller computes the vector in its own process and passes finished numbers as `embedding: list[float]`; the tools never receive the requirement's text at all.
L970 | MEASURED | Measured before this section was written: the only declared runtime dependency is `mcp`, no module of the package imports one of the socket-opening names the network gate enumerates, and the vector arrives purely as an argument.
L973 | HISTORY | The claim used to end "imports anything that could open a socket" — a claim about every socket-opener, not a list — and this was measured false: `from logging.handlers import SocketHandler`, the same module's `HTTPHandler`, and `from multiprocessing.connection import Client` all returned an empty result against the gate's own function (CB-190).
L979 | RULE | The safety claim is bounded to this package's own code and to the vector's route, and that bound is load-bearing rather than modest — it is not a claim about process capability.
L980 | BOUNDARY | It must never be written as "codebugs cannot reach the network": the `mcp` dependency carries its own network transport (server.py, an HTTP mode, this project runs over stdio), and `subprocess` is used legitimately for git, which can itself run `curl`.
L983 | RULE | What is true and checkable is two narrower statements about this package's source text, not one about its capability: (1) no module under `src/codebugs/` imports one of the socket-opening modules the gate enumerates; (2) since CB-190, no module imports anything from outside the package and the standard library that is not declared there by exact dotted name with a reason.
L987 | RULE | The vector's own route is a third, separate claim, unchanged: from the caller's argument into this tracker's SQLite file and nowhere else.
L988 | WHY-NARROW | A claim wider than its measurement is the defect class this whole direction exists to close; stating it precisely matters more than stating it loudly.
L990 | MEASURED | Importing the one declared MCP name already puts both SSE transports — `mcp.server.sse` and `mcp.client.sse` — into `sys.modules` along with most of the SDK.
L993 | BOUNDARY | What the ratchet buys is only that the source cannot NAME a second network-capable import without a row somebody reads — never that the transport itself is absent from the running process.
L994 | HISTORY | No module count is quoted in this section on purpose: an earlier draft quoted one and it was wrong, because the measuring predicate matched the prefix `mcp` without a dot and swept in `mcp_types`, a separate distribution — the exact confusion the ratchet's own table exists to prevent.
L999 | RULE | These claims are held by a gate, because a safety assertion with no gate behind it is a "gate that cannot fire" written as prose — the literal subject of CB-159/CB-160.
L1001 | IDENT | `tests/test_no_network_capability.py` walks every package module by AST and holds two mechanisms that answer different questions, neither of which may be deleted in favour of the other (CB-190).
L1003 | RULE | The test also refuses `__import__`/`importlib.import_module`/`exec`/`eval`, none of which a check that reads only import statements could see.
L1006 | RULE | The first mechanism is an enumeration of socket-opening module names, keyed on the CAPABILITY rather than on the module name.
L1007 | MEASURED | The naive name-keyed form was measured dead on arrival: `src/codebugs/db.py` carries `from urllib.request import pathname2url`, a pure string function that opens nothing, while `import urllib.request` binds the module and hands you `urlopen` — a name-keyed check would have refused a healthy package.
L1010 | RULE | A FROM-import of a network module is judged name by name against a `DECLARED_EXCEPTIONS` table carrying a reason per row, and a plain `import` of a network module is refused outright.
L1012 | BOUNDARY | Being an enumeration is this first mechanism's defining limit: a client nobody listed walks straight past it undetected.
L1013 | MEASURED | Measured: `cohere`, `ollama`, and `httplib2` were all green against the enumeration, and a planted module carrying all three left the test file reporting 25 passed.
L1017 | RULE | The second mechanism is a third-party import ratchet that is not an enumeration of what to refuse — it refuses by default and enumerates only what is ALLOWED.
L1019 | RULE | Every import whose dotted name leaves both this package and the standard library must be named in `DECLARED_THIRD_PARTY` with a reason.
L1019 | MEASURED | `DECLARED_THIRD_PARTY` holds five rows today.
L1020 | RULE | The ratchet's key is the exact dotted name, never the top-level module name — this is load-bearing twice over.
L1021 | WHY-NARROW | A top-level `mcp` row would have licensed `mcp.server.sse` and `mcp.client.sse`, so the first row of a table meant to stop network imports being parked would itself have been a parked network capability.
L1023 | WHY-NARROW | A top-level-keyed table would also be unable to go stale, since some `mcp` import always exists in this package.
L1024 | RULE | The package's own name is derived from `codebugs.__name__` and may not be written as a row in the ratchet table — such a row would lie about what the table declares and, being permanently live, would defeat self-deletion.
L1026 | RULE | Both `DECLARED_EXCEPTIONS` and `DECLARED_THIRD_PARTY` are self-deleting — a row naming an import that is no longer present fails — because otherwise a table becomes the place real network/foreign imports are parked.
L1030 | BOUNDARY | One new property is declared rather than counted as covered: before CB-190 the verdict was a pure function of the source text; since the ratchet classifies the standard library via `sys.stdlib_module_names`, the verdict is now a function of the source text AND the interpreter version.
L1033 | MEASURED | On this tree nothing currently diverges: the three foreign top-level names (`mcp`, `mcp_types`, `pydantic`) are foreign on every admitted Python version, measured on 3.11 as well as on the pinned interpreter.
L1035 | BOUNDARY | The CI matrix runs only `test_cli_signals.py` and `test_fsio.py` across 3.11–3.14, so `test_no_network_capability.py` itself executes only on the pinned interpreter, and nothing would notice if the version-agreement property stopped holding.
L1037 | HISTORY | `codebugs` is the fourth top-level name the tree imports and is deliberately absent from the "foreign" list because it is excluded by derivation, not by foreignness — an earlier draft of this sentence conflated the two, saying "four" and then listing three.
L1040 | MEASURED | `telnetlib`, `nntplib`, `asyncore`, `asynchat`, and `smtpd` are kept in the enumeration precisely because the two mechanisms disagree about them by Python version: all five are in `sys.stdlib_module_names` on 3.11 (3.12 keeps the first two), and none is on 3.13 or 3.14.
L1043 | BOUNDARY | The ratchet refuses those five modules by itself on the newer interpreters, while the enumeration is the sole catcher for them on the older ones.
L1046 | RULE | RULE (ratified 2026-08-25 with the owner's task): if an embedding provider ever lands inside this package, it must be configurable from its first day, its default must be a local option, and its binding must be visible via an existing way to ask the running system which provider it is currently pointed at.
L1048 | IDENT | The visibility model is `codebugs where` and the MCP startup preflight, per CB-11's rule "a binding you cannot see is a binding you cannot debug".
L1050 | RULE | This must not be treated as a preference or something added afterwards: a provider that ships hardcoded acquires callers before it acquires a switch.
L1051 | RULE | The rule and the gate (network-import mechanisms) are two halves of one thing — the day either `DECLARED_EXCEPTIONS` or `DECLARED_THIRD_PARTY` needs a new row is the day the visibility rule starts applying.
L1054 | HISTORY-LOADBEARING | The rule used to name `DECLARED_EXCEPTIONS` alone as its trigger, and that trigger was broken: a provider built on a client the enumeration never listed would have needed no row at all, so the rule would have sat un-armed while the provider landed (CB-190) — the ratchet is what repairs this, because a provider arrives as a dependency whatever network shape it has and therefore needs a `DECLARED_THIRD_PARTY` row by construction.
L1060 | RULE | The write validates the vector on BOTH paths — `store_embedding` AND `batch_store_embeddings` — because a rule expressed as covering one call site is this repository's most-repeated failure (CB-174).
L1062 | RULE | The vector's own unfitness (empty, non-numeric, `NaN`, `inf`) is decidable from the argument alone, so that check runs BEFORE any transaction opens — a refusal must never take the write lock.
L1064 | IDENT | `store_embedding` already packed its vector-fitness check above `db.txn` for this reason.
L1065 | RULE | Agreement with what the tracker already holds (width match) needs a read, so it is a check-then-act that must live INSIDE the same transaction as the write.
L1066 | WHY-NARROW | Outside a transaction, two concurrent writers of different vector widths could both read an empty table, both pass the check, and both write — building exactly the mixed state the check exists to prevent (the CB-24 pattern verbatim).
L1068 | RULE | `busy_timeout` cannot help here because it serializes writes only and never touches the read that precedes them; `db.txn`'s `BEGIN IMMEDIATE` takes the write lock first, which is what makes the check safe.
L1070 | RULE | A third, easy-to-miss check exists: the BATCH must be homogeneous with ITSELF, and this is not a special case of the second check — in an empty tracker there is nothing stored to compare against, so a single batch call could create a mixed state on its own.
L1073 | WHY-NARROW | All three checks' placement is pinned structurally (CB-41's reasoning): a comparison made before the lock looks correct until two writers overlap, so no test of behaviour alone can discriminate a placement defect.
L1076 | RULE | One quantity decides on both the write-guard side and the read (search) side, and it is BYTES: the write guard reads `length(embedding)` and compares byte widths exactly as `search_similar`'s `WHERE` clause does.
L1078 | WHY-NARROW | Dividing by four (component count) in the write guard would make write and read two rules a rounding apart, because a blob whose byte length is not a whole number of components can still divide to the same component count as a well-formed neighbour.
L1080 | BOUNDARY | A component-wise write guard would ACCEPT a vector beside a row that the byte-wise read guard EXCLUDES — uniform to the writer, mixed to the reader.
L1082 | RULE | `embedding_stats` reports the byte count beside the component count for the same reason: a report that folded the two would say `mixed: False` over a table that SQL itself treats as two populations.
L1084 | IDENT | This bug is the CB-22/CB-52 "two copies of one precedence table" shape recurring in a new place, and it was found by reading the change end to end as one thing rather than section by section.
L1087 | MEASURED | `NaN` was the quieter half of the CB-174 problem, unnamed by the original card: `struct.pack` accepts `NaN`, the row stores, `cosine_similarity` returns `nan`, and `nan >= min_similarity` evaluates `False`, so the row vanishes from results with no error anywhere.
L1089 | RULE | A `NaN` in the QUERY vector removes every row from a similarity search, making "nothing is similar" indistinguishable from an empty tracker — the silent-empty-queue shape (CB-19/CB-25), which this repository treats as worse than a loud failure.
L1092 | RULE | The query vector must also be validated, one step past the letter of CB-174, because the write-side fix cannot reach a `NaN` that exists only inside a caller's query, never stored.
L1095 | RULE | The read-side guard is implemented in SQL, and `cosine_similarity`'s own `raise` on mismatched widths is preserved rather than removed.
L1096 | RULE | `search_similar` folds `length(embedding) = ?` (the width, bound as a parameter, never interpolated) into its `WHERE` clause, so a foreign-width row never reaches the pairwise comparison at all.
L1097 | IDENT | The precedent for this SQL-side exclusion form is `reconcile.live_source_clause`, where an exclusion is likewise expressed in SQL rather than as a per-row Python predicate.
L1099 | RULE | The pairwise `raise` on mismatched vector widths is a ratified decision: `zip()` would silently truncate the dot product while the norms stayed full, returning a plausible wrong number instead of an error.
L1100 | WHY-NARROW | The real defect was never that refusal — it was the COMPOSITION: one foreign row aborted the whole scoring loop and discarded every row already scored, in an order nothing controls.
L1102 | RULE | Making the `raise` UNREACHABLE from the search path (via the SQL width filter) is the fix; removing the `raise` itself would have been a worse change.
L1103 | IDENT | The premise "`length()` on a BLOB counts bytes" is pinned as its own premise test, in the same style as the git and argparse behaviour premise tests elsewhere in this tree.
L1107 | BOUNDARY | The cost of the SQL width-filter guard is that excluded (foreign-width) rows become INVISIBLE from a search call; the visibility channel is `embedding_stats`, not the search result itself.
L1108 | WHY-NARROW | `search_similar` returns a plain list with nowhere to carry a count of what it silently dropped, unlike `add`'s `stripped_meta_keys`; breaking the response shape to add one would cost more than it buys.
L1110 | RULE | `embedding_stats` reports `dimensions` (which widths are present and how many rows each) and `mixed`.
L1111 | RULE | Both `dimensions` and `mixed` keys are UNCONDITIONAL, following the same discipline as `attention`/`stripped_meta_keys`: an empty result means "looked, nothing stored", never "no such channel".
L1113 | RULE | `reqs_embedding_stats` takes no input at all, and is therefore explicitly stated NOT to be a privacy surface, rather than left as an unstated omission.
L1117 | HISTORY-LOADBEARING | Adversarial review measured that on a UNIFORM tracker (every stored vector the same width — the ordinary case, and the one the write guard now guarantees), a query of a DIFFERENT width used to raise loudly from `cosine_similarity` and, once the SQL filter was added, silently returned `[]` instead — "nothing is similar" over a full tracker while `embedding_stats` reports `mixed: False`, i.e. everything looks fine, which is the very defect class this whole section exists to close, recreated by the guard meant to fix it.
L1122 | RULE | `search_similar` therefore refuses (rather than returning an empty list) on AFFIRMATIVE PROOF only: the result is empty AND the tracker holds vectors AND none of the stored vectors is this query's width.
L1123 | RULE | An empty tracker still answers `[]`, because there an empty answer is genuinely true.
L1124 | RULE | A mixed-width tracker where at least some rows matched never reaches the refusal branch, so CB-174's degrade-instead-of-fail behaviour is preserved rather than undone.
L1125 | RULE | The refusal branch keys on WIDTH mismatch specifically, never on mere emptiness, so a right-width query whose status filter simply matched nothing still returns an honest empty page.
L1127 | WHY-NARROW | General lesson: a fix aimed at closing one silent-empty-queue defect can open a different one, and only an adversary examining the COMPOSITION of the elements — not the elements individually — notices, since every element here was individually correct.
L1131 | BOUNDARY | RESIDUAL, named and not closed: once a tracker holds vectors of one width, there is no sanctioned way to change embedding model (no clear-and-re-embed operation exists).
L1132 | WHY-NARROW | Building a model-switch operation with no caller asking for it was refused on direct precedent: CB-44 declined to build the resolver seam speculatively, and CB-45 built the seam only once it had its first real consumer.
L1134 | RULE | The refusal message for a width mismatch states this limit itself, because a gate with no way out is a wall rather than a diagnostic.
L1135 | MEASURED | Measured 2026-08-25 across every reachable tracker (codebugs: 6 requirements; both autosorter trackers: 1401 each), the embedded count is 0 in all of them.
L1137 | BOUNDARY | Because the embedded count was 0 everywhere measured, CB-174's one-width-per-tracker rule was a dormant breach rather than live damage, and had no migration cost at the moment it landed.
L1141 | BOUNDARY | Residual (1): the network gate matches a call site by the literal name being called, so an indirection that hides the name — `getattr(importlib, "import_module")(...)`, or `find_spec`/`module_from_spec`/`exec_module` — is not seen by it; both forms were reproduced as bypasses, and closing them would require tracking values rather than names, a much larger check.
L1145 | BOUNDARY | Residual (2): a ZERO-LENGTH blob is accepted by the write guard as an authoritative width, so a tracker that received `store_embedding(conn, id, [])` from a pre-CB-174 version now refuses every subsequent real vector, with no clear operation to escape — the same class of residual as the model-switch lock-in, reached through a different door, bounded today by the measured zero embedded-vector population.
L1148 | BOUNDARY | Residual (3): the network-import gate reads `src/codebugs/` only, so `tools/` and `tests/` are outside its coverage by design.
L1151 | BOUNDARY | `batch_store_embeddings` is still missing half of the hardening its twin `store_embedding` received (CB-184).
L1152 | RULE | In `batch_store_embeddings`, a requirement id that does not exist is silently counted as "not stored", whereas `store_embedding` raises `KeyError` for the same case (CB-125).
L1154 | RULE | CB-174 gave the batch function the `db.txn` it needed for the width check to be an atomic check-then-act, and deliberately left the counter-vs-`KeyError` inconsistency alone as a separate, negotiated behaviour change with its own test and CHANGELOG entry.

## Claims module

L1160 | RULE | `claims.py` answers "who currently holds this entity" for findings and requirements, so parallel agents can refuse to duplicate each other's work.
L1161 | RULE | There is one table, `entity_claims`; mutual exclusion is enforced by a partial unique index on `entity_id WHERE released_at IS NULL`, so "at most one live claim per entity" is a database guarantee rather than a matter of transaction discipline.
L1163 | RULE | Release is a soft delete, so `release_reason` (`explicit` or `terminal:<status>`) remains a queryable record.
L1166 | RULE | Outcomes are enums, not booleans: `claim` returns one of `claimed | already_mine | held_by_other | entity_terminal | undetermined`; `release` returns one of `released | not_yours | not_claimed | undetermined`.
L1168 | RULE | Every response is built by the single `_response()` constructor and carries all fifteen `_COMMON_KEYS`.
L1169 | RULE | `undetermined` means the database was too contended to tell, and the correct handling is to re-issue the identical call — the underlying primitive is an idempotent upsert, so a replay converges on `already_mine` and can never double-claim.
L1172 | RULE | Ownership is the triple `(holder, holder_kind, holder_repo)`, compared NULL-safely; both `claim` and `release` authorize on the full triple, so a same-text holder of a different kind or in a different repo is treated as a different claimant.
L1175 | RULE | The discriminator for retries is `touch_count`, never a timestamp, because `utc_now()` is whole-second and a retry inside one second would be indistinguishable by clock.
L1177 | RULE | There are two layers: `_claim_core`/`_release_core` emit statements and never open or commit a transaction — this is what the terminal status-change hook calls, since it runs inside `update_finding`'s already-open transaction.
L1179 | RULE | `claim`/`release` (the public layer) wrap the core functions in `db.txn` and classify contention into the outcome enums.
L1180 | RULE | Ambient-transaction invariant: every caller of the public `claim`/`release` layer must hold a connection with no already-open transaction.
L1181 | BOUNDARY | On a connection with an implicitly-opened transaction, the claim/release write happens but nothing commits, and the call still reports success — this is unreachable today only because `server.py`'s `_conn` and every CLI handler always open a fresh connection.
L1184 | RULE | Status projection is declarative via `EntityKind.busy_status` (`in_progress` for findings, `None` for requirements, since a requirement's status vocabulary has no in-progress value and its CHECK constraint is not rebuilt).
L1186 | RULE | Any entity kind that declares `busy_status` must satisfy invariants P1–P4, documented on `EntityRef.set_status`.
L1187 | RULE | Exit codes are the API for shell callers: `0` proceed, `1` error, `3` held by someone else, `4` already resolved, `5` contended/retry.
L1188 | RULE | `codebugs claims --format ids` prints bare ids and exits 0 even on an empty list, so a shell loop needs no special-case parsing.
L1189 | IDENT | Exit code `141` was added package-wide by CB-78; it is not a claims-module outcome, and is documented in this section only because this is where the exit-code list lives — ownership belongs to the CLI section.
L1191 | RULE | `141` means `128 + SIGPIPE`: the reader of my stdout OR stderr went away; the disposition is process-wide, so `codebugs bad-verb 2>&1 | head -0` also yields it, and it can be produced by any verb.
L1193 | WHY-NARROW | `141` is deliberately distinguishable from `1` — this is the entire reason the alternative "silent exit 0" design was rejected, since `codebugs export-csv /dev/stdout | gzip > backup.gz` whose `gzip` dies must never report success over a truncated backup.
L1196 | RULE | A `| while read` loop that `break`s now kills the producer at exit 141 rather than 1; both are non-zero, so no `set -e` script's behaviour changes.
L1198 | BOUNDARY | `141` is observable only when the reader closes without draining any amount of output, or when un-drained output exceeds the 64 KB pipe buffer.
L1199 | IDENT | Exit code `74` was added by CB-136; it is also not a claims outcome, recorded here for the same reason, owned by the CLI section.
L1200 | RULE | `74` is `EX_IOERR` from `sysexits(3)`: my output could not be WRITTEN on a descriptor that was healthy at process entry (`/dev/full`, a filesystem that filled mid-run, a wedged PTY).
L1202 | RULE | `74` deliberately asserts nothing about whether the command's underlying effect landed, because the write that failed is usually the line reporting a mutation that has already committed.
L1204 | RULE | `74` replaces the two codes the program previously produced for this case: `1` unbuffered with a raw traceback, and `120` block-buffered with "Exception ignored while flushing sys.stdout" — the first of which was this package's code for BAD INPUT, now misapplied over a landed write (the CB-15/CB-16 lie).
L1207 | WHY-NARROW | `141` is deliberately not reused for the full-medium case: there the reader is gone, here the reader is present and the medium is full, and blurring the two would undo CB-78's own distinction inside CB-136's fix.
L1208 | RULE | When a verb had already chosen its own non-zero exit code, `74` wins over it, since the caller never received the output that the verb's own code was meant to describe.
L1210 | BOUNDARY | Three limits on the `74` design are each measured rather than assumed, because the first draft of this paragraph overclaimed and cross-model review found it.
L1211 | MEASURED | `EPIPE` is excluded and still reports `141`: `cli.run` restores the SIGPIPE disposition but cannot clear an inherited signal mask, so a caller that had blocked SIGPIPE gets `EPIPE` back from the write instead of dying by signal.
L1213 | WHY-NARROW | Classifying that `EPIPE` case as "the medium is full" (i.e. as `74`) would undo CB-78's own fix from inside CB-136's fix, so it stays `141`.
L1214 | BOUNDARY | `74` covers only what goes through `sys.stdout` (`print` and the `csv` writer — every verb's ordinary output) and NOT `export-csv <path>`, where `fsio.atomic_write` writes through its own file object and CB-76's arm still reports exit 1 (this includes `export-csv /dev/stdout`).
L1217 | RULE | That `export-csv <path>` divergence from `74` is unchanged, pre-existing behaviour rather than a hole this change opened, and nothing is committed on that path, so it is not the CB-15/CB-16 lie.
L1218 | RULE | A verb that CRASHES keeps its own traceback and its own exit code, so a still-buffered stdout can still reach `120` there as before — trading a crash's traceback for a tidy code is judged the worse of the two outcomes.
L1221 | RULE | Adoption: autosorter's `worktree-setup.sh` claims every card named in the branch name (and in `--items`) BEFORE `git worktree add`, with an EXIT trap that releases them if setup aborts; `worktree-finish.sh` releases whatever the branch still holds.
L1223 | RULE | Exactly one of those calls is allowed to be fatal — the setup gate; everything else is guarded, so a missing or contended tracker can never abort a finish after the merge has already landed.
L1226 | IDENT | This repo's own `tools/worktree-*.sh` scripts follow the same shape, per CB-58; see the Workflow section for the exit-code handling and the trap.
L1227 | WHY-NARROW | The fatal/guarded asymmetry between setup and finish is about WHEN, not about relative importance: setup may abort freely because nothing has been created yet, while finish runs after the merge has landed, where a false failure over mere tracker bookkeeping is the worse outcome.
L1232 | RULE | codebugs deliberately diverges from `FINAL-DESIGN.md` §6.2–§6.3 in two places, both because that section was written for autosorter's own script and one of its premises does not hold in this repository — neither divergence should be "fixed" back without reading the reasoning first.
L1235 | RULE | Divergence 1: `--allow-duplicate` does NOT clear a `held_by_other` (exit 3) refusal, unlike the design doc's §6.2(a) which has it clear both `3` and `4`.
L1236 | WHY-NARROW | That flag also clears the pure-git branch-type guard, and since this repo never deletes merged branches, the flag is needed for ordinary follow-up work — collapsing both jobs into one flag would turn the claim gate off exactly during normal work.
L1238 | RULE | `CODEBUGS_SETUP_NO_CLAIM=1` is the typed escape-hatch alternative to `--allow-duplicate`, and it builds with NO claim at all rather than stealing an existing one.
L1239 | MEASURED | This divergence was ratified by the owner on 2026-08-19, against the design doc, on the stated reasoning.
L1241 | RULE | Divergence 2: finish leaves restore ON, unlike the design doc's §6.3 which passes `--no-restore`.
L1242 | WHY-NARROW | In the original design, `[7b/9] auto-resolve-codebugs.py` had already flipped the card to `fixed` via a `Fixes:` trailer before release, making the release step a no-op, so `--no-restore` guarded only a rare case there.
L1244 | WHY-NARROW | This repo has no auto-resolve step — `worktree-finish.sh` tells the operator to close the card by hand — so the card is typically still `in_progress` at finish time, and inheriting `--no-restore` would leave every finished branch's card `in_progress` with no holder, reintroducing CB-58's own original defect via CB-58's own fix.
L1247 | RULE | Restore is implemented as a CAS against the projected status value, so it still cannot resurrect a card someone already closed manually — that case returns `not_claimed` at exit 0 and writes nothing.
L1250 | BOUNDARY | Deferred by design, not forgotten: `steal`, claim history queries, audit/divergence tooling, retention, `expected_status`/`changed`, and `pull_next` integration.
L1252 | IDENT | Full deferred-scope list is documented at `docs/superpowers/plans/design-council-entity-claims/FINAL-DESIGN.md` §10.

## Milestones module

L1256 | RULE | Releases (e.g. "release/1.1") and standing streams (e.g. "stream/triage", "stream/maintenance", "stream/security") give parallel-agent work a durable bucket to land in.
L1256 | RULE | `milestones.py` owns four tables — `milestones`, `milestone_items`, `milestone_audit`, `agent_capacity` — and 20 MCP tools spread across three phases.
L1258 | RULE | Phase 1 (Foundation): milestone & item CRUD, an audit log, and auto-routing of every new finding into `stream/triage` (or into `stream/security` when `severity=critical` and `category` starts with `"security:"`).
L1259 | RULE | Phase 2 (Triage + pull): `triage_inbox`/`triage_dismiss`/`triage_promote`, plus `pull_next(agent_id, capacity)`, which atomically claims the highest-priority eligible item for the calling agent.
L1259 | RULE | `pull_next`'s concurrency is enforced by `db.txn` (CB-40) and it refuses to run under an ambient (already-open) transaction.
L1259 | HISTORY | `pull_next` no longer copies `merge.py`'s old raw save/restore `isolation_level` pattern, which had a commit hazard; `merge.py` itself no longer has that pattern either.
L1259 | RULE | `pull_next` returns the claimed row directly from the UPDATE statement's `RETURNING` clause rather than re-reading it by `item_ref` after commit (CB-39).
L1260 | RULE | Phase 3 (Close gate + branch tracking): `mark_branch_only(item, branch)`/`mark_integrated(item, commit)` keep the release container's state honest.
L1260 | RULE | `milestone_close` refuses to close over unfinished, branch-only, or blocker-gated items unless `force=True` is set, and a forced close logs its reason.
L1260 | RULE | Streams (as opposed to releases) cannot be closed at all.
L1262 | RULE | `pull_next` eligibility requires: the item is `open`; it has no active blockers (this check is skipped for `item_kind='external'`); acceptance is required for `size='large'` items; and large bugs inside release milestones must declare `linked_frs` whose ids resolve to real rows in `requirements`.
L1262 | RULE | Agent capacity is tracked per `(agent_id, size)` pair and is decremented by `release_item`.
L1264 | IDENT | Design and adversarial-review history for the milestones module live at `docs/superpowers/plans/2026-05-11-milestones-streams.md` and the source spec `../autosorter/.claude/plans/codebugs-milestones-streams-v1.md`.

## VERBATIM-CRITICAL

L955 | `reqs_query --section "Architecture Migration"` / `reqs_query(section="Architecture Migration")`
L957 | ARCH-001, ARCH-002, ARCH-003, ARCH-004, ARCH-005
L960 | `register_schema()`, `register_tool_provider()`, `register_cli_provider()`, `db.connect()`, `server.py`, `cli.py`
L961 | `_ensure_modules_loaded()`, `db.py`
L962 | `SERVER_NAMES`, `--mode`
L969 | `embedding: list[float]`
L973 | CB-190
L975 | `from logging.handlers import SocketHandler`, `HTTPHandler`, `from multiprocessing.connection import Client`
L985 | `DECLARED_EXCEPTIONS`, `DECLARED_THIRD_PARTY`
L990 | `mcp.server.sse`, `mcp.client.sse`, `sys.modules`
L999 | CB-159, CB-160
L1001 | `tests/test_no_network_capability.py`
L1003 | `__import__`, `importlib.import_module`, `exec`, `eval`
L1007 | `src/codebugs/db.py`, `from urllib.request import pathname2url`, `import urllib.request`, `urlopen`
L1013 | `cohere`, `ollama`, `httplib2`
L1019 | `DECLARED_THIRD_PARTY`
L1024 | `codebugs.__name__`
L1032 | `sys.stdlib_module_names`
L1033 | `mcp`, `mcp_types`, `pydantic`
L1035 | `test_cli_signals.py`, `test_fsio.py`
L1040 | `telnetlib`, `nntplib`, `asyncore`, `asynchat`, `smtpd`
L1046 | CB-11
L1048 | `codebugs where`
L1054 | CB-190
L1060 | `store_embedding`, `batch_store_embeddings`, CB-174
L1064 | `db.txn`
L1068 | `busy_timeout`, `BEGIN IMMEDIATE`
L1073 | CB-41
L1076 | `length(embedding)`, `search_similar`, `WHERE`
L1082 | `embedding_stats`
L1084 | CB-22, CB-52
L1087 | `struct.pack`, `cosine_similarity`, `nan >= min_similarity`
L1090 | CB-19, CB-25
L1096 | `length(embedding) = ?`
L1097 | `reconcile.live_source_clause`
L1099 | `zip()`
L1103 | premise test (unnamed literally, referenced conceptually)
L1108 | `stripped_meta_keys`, `add`
L1110 | `dimensions`, `mixed`
L1111 | `attention`
L1113 | `reqs_embedding_stats`
L1131 | CB-174
L1132 | CB-44, CB-45
L1135 | 2026-08-25; codebugs 6 requirements; autosorter trackers 1401 each; embedded count 0
L1141 | `getattr(importlib, "import_module")(...)`, `find_spec`, `module_from_spec`, `exec_module`
L1146 | `store_embedding(conn, id, [])`
L1148 | `src/codebugs/`, `tools/`, `tests/`
L1151 | CB-184
L1152 | `KeyError`, CB-125
L1161 | `entity_claims`, `entity_id WHERE released_at IS NULL`
L1163 | `release_reason`, `explicit`, `terminal:<status>`
L1166 | `claimed`, `already_mine`, `held_by_other`, `entity_terminal`, `undetermined`, `released`, `not_yours`, `not_claimed`
L1168 | `_response()`, `_COMMON_KEYS` (fifteen)
L1172 | `(holder, holder_kind, holder_repo)`
L1175 | `touch_count`, `utc_now()`
L1177 | `_claim_core`, `_release_core`, `claim`, `release`, `update_finding`
L1184 | `EntityKind.busy_status`, `in_progress`
L1186 | `EntityRef.set_status`, P1–P4
L1187 | exit codes: `0`, `1`, `3`, `4`, `5`
L1188 | `codebugs claims --format ids`
L1189 | `141`, CB-78
L1191 | `128 + SIGPIPE`, `codebugs bad-verb 2>&1 | head -0`
L1195 | `codebugs export-csv /dev/stdout | gzip > backup.gz`
L1198 | 64 KB pipe buffer
L1199 | `74`, CB-136
L1200 | `EX_IOERR`, `sysexits(3)`, `/dev/full`
L1204 | `1`, `120`, "Exception ignored while flushing sys.stdout", CB-15, CB-16
L1211 | `EPIPE`, `cli.run`, SIGPIPE
L1214 | `sys.stdout`, `export-csv <path>`, `fsio.atomic_write`, CB-76, `export-csv /dev/stdout`
L1221 | `worktree-setup.sh`, `--items`, `worktree-finish.sh`
L1226 | `tools/worktree-*.sh`, CB-58
L1232 | `FINAL-DESIGN.md` §6.2–§6.3
L1235 | `--allow-duplicate`, §6.2(a)
L1238 | `CODEBUGS_SETUP_NO_CLAIM=1`
L1239 | 2026-08-19
L1241 | `--no-restore`, §6.3
L1242 | `[7b/9] auto-resolve-codebugs.py`, `Fixes:` trailer
L1247 | `not_claimed`
L1252 | `docs/superpowers/plans/design-council-entity-claims/FINAL-DESIGN.md` §10
L1256 | `milestones`, `milestone_items`, `milestone_audit`, `agent_capacity`, "release/1.1", "stream/triage", "stream/maintenance", "stream/security"
L1258 | `stream/triage`, `stream/security`, `severity=critical`, `category.startswith("security:")`
L1259 | `triage_inbox`, `triage_dismiss`, `triage_promote`, `pull_next(agent_id, capacity)`, `db.txn`, CB-40, `merge.py`, `RETURNING`, `item_ref`, CB-39
L1260 | `mark_branch_only(item, branch)`, `mark_integrated(item, commit)`, `milestone_close`, `force=True`
L1262 | `open`, `item_kind='external'`, `size='large'`, `linked_frs`, `requirements`, `agent_capacity`, `(agent_id, size)`, `release_item`
L1264 | `docs/superpowers/plans/2026-05-11-milestones-streams.md`, `../autosorter/.claude/plans/codebugs-milestones-streams-v1.md`
