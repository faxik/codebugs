L829 | RULE | Releasing step 1: bump `version` in pyproject.toml and `__version__` in src/codebugs/__init__.py.
L830 | IDENT | tests/test_release_version.py refuses a disagreement between those two version fields.
L830 | RULE | The version-agreement check covers the installed distribution as well, not just the source files.
L831 | RULE | Releasing step 2: retitle `## [Unreleased]` in CHANGELOG.md to `## [X.Y.Z] — <date>`.
L832 | RULE | Releasing step 2 (cont.): leave an empty `## [Unreleased]` section above the retitled one.
L832 | RULE | Releasing step 2 (cont.): open the new version section with a highlights paragraph written for a user (not for agents).
L833 | RULE | Releasing step 3: tag the merge commit only AFTER the branch has landed, from the primary checkout, with `git tag -a vX.Y.Z <merge-sha>`.
L834 | WHY-NARROW | Tagging must happen after landing and from the primary checkout because a tag made on the branch itself points at a commit that never landed on main.
L838 | RULE | Domain modules live under src/codebugs/: db.py owns findings plus shared infrastructure; reqs.py, bench.py, blockers.py, merge.py, and sweep.py are separate domain modules.
L838 | RULE | embeddings.py provides vector storage and similarity search, delegating from reqs.
L838 | RULE | milestones.py implements releases, standing streams, and capacity-aware pull.
L839 | RULE | types.py holds shared entity constants (statuses, priorities, severities), resolver functions, and terminal states.
L839 | RULE | types.py is zero-dependency and therefore safe to import from anywhere in the package.
L840 | RULE | server.py is a thin MCPServer orchestrator that discovers tool providers via the registry and filters them by the --mode flag.
L840 | MEASURED | server.py is described as approximately 48 lines.
L840 | RULE | The MCP server requires the mcp 2.x SDK, specifically mcp.server.mcpserver.MCPServer, which replaced 1.x's mcp.server.fastmcp.FastMCP.
L841 | RULE | cli.py is a thin argparse orchestrator that discovers CLI providers via the registry and filters them by the --mode flag.
L841 | RULE | cli.py has two entry points, and the split between them is load-bearing.
L841 | RULE | main() is the importable body of the CLI; three test modules call it in-process.
L841 | RULE | run() is what [project.scripts] and `python -m codebugs.cli` actually reach.
L841 | RULE | run() first restores the POSIX SIGPIPE disposition (CB-78) and then refuses to run at all when stdout is already closed (CB-134).
L841 | HISTORY-LOADBEARING | This line used to claim cli.py was "~40 lines"; it was actually 159 lines before the change that added the run()/main() split and CB-134's stdout check, and is larger still now, so the line-count claim is dropped from the document rather than re-guessed.
L842 | RULE | fmt.py provides shared CLI output utilities (ASCII table formatting) and produces text for a stream only, nothing else.
L842 | RULE | File writing deliberately does not live in fmt.py (CB-76).
L843 | RULE | fsio.py's atomic_write is the only sanctioned way a CLI handler writes a file.
L843 | RULE | fsio.py owns tempfile lifecycle, destination classification, and atomic replacement.
L843 | RULE | fsio.py imports nothing from the rest of the package.
L844 | RULE | Storage is a single SQLite database at .codebugs/findings.db, and each domain module owns its own schema via ensure_schema(conn).
L848 | RULE | provenance.py owns the staleness and commit-trailer logic (file_status, check_findings, resolve_trailers), registers its own MCP tools and CLI, and is a first-class --mode.
L848 | HISTORY | This debt entry previously claimed the staleness/provenance logic still lived in db.py long after it had actually moved to provenance.py (CB-4); the entry is now marked "done, with one seam left".
L848 | BOUNDARY | A remaining seam is judged not worth its own card yet: provenance.head_sha only delegates to db.git_rev_parse and has no callers, while findings' own provenance auto-capture (findings.py:546, :581) calls db.git_rev_parse directly rather than going through provenance.head_sha.
L849 | RULE | _ensure_modules_loaded() still imports every known domain module so that their register_schema(), register_tool_provider(), and register_cli_provider() calls execute.
L849 | MEASURED | All three registries (schema, tool provider, CLI provider) are complete, per ARCH-001 + ARCH-002 + ARCH-004.
L849 | BOUNDARY | This import-trigger mechanism is temporary and will eventually be replaced by auto-discovery.
L850 | RULE | blockers.py calls the public db.row_to_dict() (at blockers.py:87, :307, :442) and does not reach into reqs at all.
L850 | MEASURED | No private _row_to_dict function exists anywhere in the package.
L850 | HISTORY | The debt entry describing blockers.py's reach into a private _row_to_dict had already been resolved (CB-5) and outlived the code it described.
L851 | RULE | Findings' MCP tools (add, query, stats, etc.) lack the domain prefix that most other modules use (e.g. reqs_add, codebench_import), because the findings domain predates the naming conventions.
L851 | WHY-NARROW | Renaming MCP tools would be a breaking change for clients, so the findings tools keep their unprefixed names rather than being renamed to conform.
L851 | BOUNDARY | Findings is not the only unprefixed case — provenance.py also exposes an unprefixed staleness_check — so the domain-prefix rule should be read as governing new tools going forward, not as an invariant that currently holds everywhere.
L852 | RULE | The milestones spec mandates spec-canonical tool names — pull_next, release_item, triage_dismiss, mark_branch_only, wip_status — kept verbatim (unprefixed) because external consumers (autosorter's worktree-setup.sh / worktree-finish.sh) call them by these exact names.
L852 | RULE | Milestone management tools (milestone_create, milestone_status, milestone_close, ...) do carry the domain prefix, unlike the spec-canonical set.
L853 | RULE | db.register_post_add_hook(name, fn) is the extension point letting milestones.auto_route_finding run inside add_finding / batch_add_findings before the final commit, so the finding and its stream/triage link land atomically.
L853 | RULE | Other modules may register additional post-add hooks beyond the milestones one.
L853 | RULE | Since CB-43, post-add hooks fire only on genuine inserts — a deduplicated observation (a bump or reopen) does not re-fire them, because the matched card already has its projection.
L854 | HISTORY-LOADBEARING | The pre-add resolver seam was built only once it had a real first consumer (CB-45); CB-44 had earlier refused to build the seam speculatively, and that refusal is why the seam waited.
L854 | RULE | db.register_pre_add_resolver(name, fn, *, meta_keys, updatable_keys=()) runs resolvers inside _add_one's insert path under an annotate-only contract: a resolver returns a meta patch or None.
L854 | RULE | Redirect is deliberately inexpressible in the pre-add resolver contract, because identity is core (CB-44 ratified); a future redirect variant would require a newly negotiated contract.
L854 | RULE | The resolver runner's never-commit contract is mechanically enforced, not merely documented: the runner refuses to run at all outside an open transaction.
L854 | MEASURED | Outside an open transaction, SAVEPOINT+RELEASE is itself a commit — verified.
L854 | RULE | The runner detects a resolver that closed the caller's transaction, checking after every call and again after its own RELEASE, and raises outside the failure-swallow when this happens.
L854 | RULE | The runner guards its own ROLLBACK TO cleanup so that cleanup can never mask the real underlying error.
L854 | HISTORY-LOADBEARING | All three enforcement mechanisms (refusing outside a transaction, detecting a resolver closing the caller's transaction, guarding the ROLLBACK TO cleanup) were cross-model review findings, meaning each closes a real failure mode review caught.
L854 | RULE | Each resolver runs under a SAVEPOINT named with a per-call nonce plus an index.
L854 | HISTORY-LOADBEARING | Index-only savepoint naming (without the nonce) let a resolver commit and recreate the runner's savepoint under its predictable name, turning the runner's own RELEASE into an actual commit — a defect caught by Codex diff review, which is why the nonce component exists.
L854 | RULE | A resolver failure rolls back only that resolver's own writes and is stamped queryably into meta.resolver_errors, retrievable via query(meta_key="resolver_errors").
L854 | BOUNDARY | stderr is only the immediate channel for a resolver failure; the durable, queryable record is meta.resolver_errors.
L854 | RULE | Each resolver receives a deep copy of the observation, and validated resolver outcomes are snapshotted into the patch.
L854 | HISTORY-LOADBEARING | Without the deep copy and snapshot, a shared reference let a failing resolver poison the runner's own error-stamp field (the runner reads `at` for its error stamp and the patch becomes the row's meta), aborting the add during serialization.
L854 | RULE | Resolver outcomes are validated inside the savepoint (must be a dict, with string keys, passing json.dumps(..., allow_nan=False)) so that a bad patch can never abort the add later during meta_final serialization.
L854 | RULE | Both db.resolver_reserved_meta_keys() and run_pre_add_resolvers() call _ensure_modules_loaded() first, so that neither the reserved-key set nor the resolver registry depends on which modules the current process happened to import.
L854 | HISTORY-LOADBEARING | Before this call was added, a bare library connection whose meta=None add path never touched the reserved set could run with an empty resolver registry and silently skip annotation entirely.
L854 | RULE | The pre-add resolver firing rule is exactly one predicate: finding_id is None and annotate; an explicit finding_id bypasses the whole observation machinery.
L854 | RULE | dedup_action is context only, never the firing predicate — an explicit-id insert also carries dedup_action "created".
L854 | RULE | CSV import passes annotate=False, because an import is not an observation, and strips the dynamic reserved-key union from stored meta.
L854 | WHY-NARROW | Without that stripping on CSV import, a previously annotated export would be refused when re-imported.
L855 | RULE | db.register_status_change_hook(name, fn) is the update-side twin of the post-add hook, sharing the same registration discipline, in-transaction contract, and swallow-and-log policy.
L855 | RULE | findings.update_finding and reqs.update_requirement fire the status-change hook only when the write actually changed the row: a status was requested, rowcount == 1, and the value differs from the prior one.
L855 | RULE | claims._auto_release_on_terminal uses the status-change hook to close a claim in the same transaction as the status write that resolved the entity.
L855 | RULE | milestones.reconcile._reconcile_on_terminal uses the status-change hook to project the entity's new status onto its milestone items.
L856 | RULE | A derived queue must never be trusted to a write-time hook alone (CB-26).
L856 | MEASURED | Before the fix, 19 of 23 open stream/triage rows pointed at already-terminal findings, so triage_inbox was roughly 83% stale and pull_next could hand an agent already-finished work.
L856 | RULE | milestone_items is a projection of a finding or requirement, but routing used to run only once at add time, and nothing moved the item when its source later resolved.
L856 | RULE | The write-time hook is only half the freshness fix; the other, necessary half is a read-side filter.
L856 | IDENT | Since CB-31 the read-side filter is reconcile.live_source_clause, a single SQL fragment, replacing a per-row Python predicate applied by hand.
L856 | RULE | The read-side filter's call sites are enumerated by the test TestLiveSourceClauseCallSites, not counted in this document's prose.
L856 | HISTORY | A call-site count was once stated in this document's prose and went stale, which is why the count now lives only in the test.
L856 | RULE | The read-side filter is not merely belt-and-braces — it is the only reason the milestone-items freshness invariant holds, because five writers bypass the write-time hook entirely.
L856 | RULE | add_milestone_item inserts a new item as status open regardless of the source's actual status, bypassing the hook.
L856 | RULE | set_item_status and release_item(status='abandoned') can reopen an item, bypassing the hook.
L856 | RULE | The requirements bulk importer and the markdown importer write statuses without going through update_requirement, bypassing the hook.
L856 | RULE | EntityRef.set_status deliberately never fires hooks.
L856 | RULE | General rule: eager reconciliation keeps stored state honest, but only a read-side filter can make a freshness guarantee, and every bypass writer must be enumerated before claiming that guarantee holds.
L856 | RULE | Trap 1: the filter must select by item_kind as well as item_ref, because _validate_item_ref skips externals and the UNIQUE constraint includes item_kind, so (bug, CB-1) and (external, CB-1) are both legal rows and only the bug one is a genuine projection.
L856 | RULE | Trap 2: the terminal predicate must be "status != target", not "not terminal" — both updaters permit fixed → wont_fix, which must remap done → dismissed — while deferred is excluded because no queue returns a deferred row and closing one would only destroy the deferral record.
L856 | RULE | Trap 3: capacity must be decremented before clearing assigned_agent, since the row is the only record of who held the slot.
L856 | RULE | Trap 4: run_status_change_hooks swallows its own failures while the caller still commits, so a multi-row hook needs its own SAVEPOINT or it commits a partial reconciliation behind a success-shaped return value.
L856 | BOUNDARY | "It logs to stderr, so it's visible" is false for an MCP caller, which is why a hook failure is recorded as an audit row instead of relying on stderr.
L856 | RULE | The milestone-reconciliation status-change hook is scoped to kind='stream' only, because milestone_close's unfinished gate reads only item status and done_commit is never itself a gate, so auto-marking a release item done would let a release close over a missed integration (CB-32).
L858 | RULE | The read-side filter is now packaged as a seam, and its justification is the write-lock contention cost it removes, not anti-drift.
L858 | MEASURED | source_is_terminal cost two queries per row (a sqlite_master probe plus a status SELECT), and capacity._candidates ran it per candidate row inside pull_next's BEGIN IMMEDIATE window, so every concurrent agent waited behind it.
L858 | IDENT | reconcile.live_source_clause(conn, *, alias) folds the exclusion into one SQL fragment.
L858 | WHY-NARROW | Anti-drift alone would not have justified a new API: an AST ratchet over the existing call sites would buy the same "record the decision" property with no new SQL, so the seam must earn its keep on the write-lock cost savings instead.
L858 | RULE | Sub-rule 1: use NOT EXISTS, never `status NOT IN (...)` over a LEFT JOIN or scalar subquery, because a missing source row yields NULL, `NULL NOT IN (...)` is NULL, and `WHERE NULL` excludes — inverting fail-open into a queue that silently hides work.
L858 | RULE | A row may be hidden by the read-side filter only on affirmative proof: a recognised kind, an existing table, a matching row, and a terminal status.
L858 | RULE | Sub-rule 2: `alias` is a required, bare-identifier, validated parameter to live_source_clause.
L858 | WHY-NARROW | Left unqualified, the correlated item_kind/item_ref would resolve against the source table first instead of the outer query — harmless today only because findings and requirements happen to lack those column names.
L858 | MEASURED | Measured with an item_kind column added to findings: the subquery stopped referencing the outer item_kind and hid a live external row — failing closed by hiding live work.
L858 | IDENT | EntityKind validates its `table` field at construction (CB-22), but nothing validated `alias` before this rule was added.
L858 | RULE | Sub-rule 3: build the exclusion clause once per traversal, not per candidate row, because per-bucket construction adds eight sqlite_master reads inside the exclusive-lock hold, making the original contention defect worse.
L858 | RULE | Sub-rule 4: the set-wise spelling of the terminal predicate lives on EntityKind (EntityKind.terminal_exists_sql), beside the row-wise EntityRef.is_resolved, because entities.py is the module that owns those tables, the status column, and the terminal vocabulary.
L858 | HISTORY-LOADBEARING | CB-31's first implementation built the subquery inside milestones/ instead, reaching into another module's tables, bypassing the readable_cols allowlist, and carrying a `# noqa: S608` justified by validation living in a file that did not own it — three of this document's own rules broken by one function, which is why co-location on EntityKind was chosen.
L858 | RULE | Co-location on EntityKind is the actual anti-drift mechanism; the accompanying differential test is a sample, not a proof.
L858 | BOUNDARY | The differential test would miss a change to one side of the comparison that every fixture row happens to agree on.
L858 | MEASURED | The differential test is non-vacuous only because of two specific fixture rows — an external item pointing at a terminal finding, and a bug item whose source row is missing — and the realistic NULL-unsafe mutant is caught by exactly those two rows and by nothing else.
L858 | BOUNDARY | A fixture claiming "externals are covered" whose external item points at a live (non-terminal) finding would prove nothing about this defect.
L858 | RULE | Sub-rule 5: the caller owns null-safety on its own discriminator — the live_source_clause fragment itself is never NULL, but ANDing it with `item_kind = ?` can be, and `NOT NULL` is NULL, so `WHERE NULL` excludes; use `IS` instead.
L858 | BOUNDARY | Two callers are deliberately left unfiltered by the read-side exclusion: get_milestone_status (a rollup that reports stored state as-is) and the milestone close gate's unfinished-items check, where a false refusal is the correct, safer behavior (CB-32).
L859 | BOUNDARY | Rejected alternative: implementing the read-side filter as a SQL VIEW.
L859 | WHY-NARROW | The obvious objection to a VIEW — that its DDL would hardcode the terminal-status sets — is false, since it could be regenerated from kind.terminal on every schema init; the real reason for rejection is that CREATE VIEW over a missing source table succeeds while the first SELECT from it then raises "no such table", so a view fails closed with a crash for exactly the raw-connection callers this design must keep working.
L864 | RULE | Each domain module owns its own schema, constants, and public functions; no module should reach directly into another module's tables.
L865 | RULE | db.py is infrastructure: it provides connect(), ID generation, and findings CRUD, and must not import domain modules at the top level.
L866 | RULE | Domain modules may import db for connection and ID utilities but must not import each other's private functions — only public interfaces.
L869 | RULE | The codebase targets Python 3.11+ and requires type hints on all public function signatures.
L870 | RULE | ruff is used for linting and formatting, with a line length of 100.
L871 | RULE | Public functions use keyword-only arguments after conn: def f(conn, *, name, ...).
L872 | RULE | MCP tool functions are prefixed with their domain name, e.g. codebench_import, reqs_add, blockers_check.
L872 | REPEAT | Exception: findings tools lack the domain prefix — already stated at L851.
L873 | RULE | CLI handlers are named cmd_<domain>_<action>().
L873 | REPEAT | Exception: findings handlers lack the domain prefix — already stated at L851.

## VERBATIM-CRITICAL

- `version` (pyproject.toml) — L829
- `__version__` (src/codebugs/__init__.py) — L829
- `tests/test_release_version.py` — L830
- `## [Unreleased]` — L831
- `CHANGELOG.md` — L831
- `## [X.Y.Z] — <date>` — L831
- `git tag -a vX.Y.Z <merge-sha>` — L834
- `db.py` — L838, L865
- `reqs.py` — L838
- `bench.py` — L838
- `blockers.py` — L838, L850
- `merge.py` — L838
- `sweep.py` — L838
- `embeddings.py` — L838
- `milestones.py` — L838, L852
- `types.py` — L839
- `server.py` — L840
- `mcp.server.mcpserver.MCPServer` — L840
- `mcp.server.fastmcp.FastMCP` — L840
- `--mode` — L840, L841
- `cli.py` — L841
- `main()` — L841
- `run()` — L841
- `[project.scripts]` — L841
- `python -m codebugs.cli` — L841
- `SIGPIPE` — L841
- `CB-78` — L841
- `CB-134` — L841
- `fmt.py` — L842
- `CB-76` — L842
- `fsio.py` — L843
- `atomic_write` — L843
- `.codebugs/findings.db` — L844
- `ensure_schema(conn)` — L844
- `provenance.py` — L848, L851
- `file_status` — L848
- `check_findings` — L848
- `resolve_trailers` — L848
- `CB-4` — L848
- `provenance.head_sha` — L848
- `db.git_rev_parse` — L848
- `findings.py:546` — L848
- `findings.py:581` — L848
- `_ensure_modules_loaded()` — L849, L854
- `register_schema()` — L849
- `register_tool_provider()` — L849
- `register_cli_provider()` — L849
- `ARCH-001` — L849
- `ARCH-002` — L849
- `ARCH-004` — L849
- `db.row_to_dict()` — L850
- `blockers.py:87` — L850
- `blockers.py:307` — L850
- `blockers.py:442` — L850
- `_row_to_dict` — L850
- `CB-5` — L850
- `add`, `query`, `stats` — L851
- `reqs_add` — L851, L872
- `codebench_import` — L851, L872
- `staleness_check` — L851
- `pull_next` — L852, L856
- `release_item` — L852
- `triage_dismiss` — L852
- `mark_branch_only` — L852
- `wip_status` — L852
- `worktree-setup.sh` — L852
- `worktree-finish.sh` — L852
- `milestone_create` — L852
- `milestone_status` — L852
- `milestone_close` — L852, L856
- `db.register_post_add_hook(name, fn)` — L853
- `milestones.auto_route_finding` — L853
- `add_finding` — L853
- `batch_add_findings` — L853
- `CB-43` — L853
- `db.register_pre_add_resolver(name, fn, *, meta_keys, updatable_keys=())` — L854
- `_add_one` — L854
- `CB-45` — L854
- `CB-44` — L854
- `SAVEPOINT` — L854
- `RELEASE` — L854
- `ROLLBACK TO` — L854
- `meta.resolver_errors` — L854
- `query(meta_key="resolver_errors")` — L854
- `json.dumps(..., allow_nan=False)` — L854
- `db.resolver_reserved_meta_keys()` — L854
- `run_pre_add_resolvers()` — L854
- `finding_id is None and annotate` — L854
- `dedup_action` — L854
- `"created"` — L854
- `annotate=False` — L854
- `db.register_status_change_hook(name, fn)` — L855
- `findings.update_finding` — L855
- `reqs.update_requirement` — L855, L856
- `rowcount == 1` — L855
- `claims._auto_release_on_terminal` — L855
- `milestones.reconcile._reconcile_on_terminal` — L855
- `CB-26` — L856
- `milestone_items` — L856
- `triage_inbox` — L856
- `CB-31` — L856, L858
- `reconcile.live_source_clause` — L856, L858
- `TestLiveSourceClauseCallSites` — L856
- `add_milestone_item` — L856
- `set_item_status` — L856
- `release_item(status='abandoned')` — L856
- `EntityRef.set_status` — L856
- `_validate_item_ref` — L856
- `UNIQUE` — L856
- `(bug, CB-1)` — L856
- `(external, CB-1)` — L856
- `fixed → wont_fix` — L856
- `done → dismissed` — L856
- `deferred` — L856
- `assigned_agent` — L856
- `run_status_change_hooks` — L856
- `kind='stream'` — L856
- `done_commit` — L856
- `CB-32` — L856, L858
- `source_is_terminal` — L858
- `sqlite_master` — L858
- `capacity._candidates` — L858
- `BEGIN IMMEDIATE` — L858
- `reconcile.live_source_clause(conn, *, alias)` — L858
- `NOT EXISTS` — L858
- `status NOT IN (...)` — L858
- `NULL NOT IN (...)` — L858
- `WHERE NULL` — L858
- `alias` — L858
- `item_kind` — L858
- `item_ref` — L858
- `EntityKind` — L858
- `CB-22` — L858
- `EntityKind.terminal_exists_sql` — L858
- `EntityRef.is_resolved` — L858
- `entities.py` — L858
- `readable_cols` — L858
- `# noqa: S608` — L858
- `IS` — L858
- `NOT NULL` — L858
- `get_milestone_status` — L858
- `CREATE VIEW` — L859
- `"no such table"` — L859
- `def f(conn, *, name, ...)` — L871
- `blockers_check` — L872
- `cmd_<domain>_<action>()` — L873
