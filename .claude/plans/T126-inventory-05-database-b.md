Inventory: CLAUDE.md lines 898–913 (worktree docs-t126-claude-md-compression)

L898 | RULE | A SQL statement either carries RETURNING and its outcome is read by fetching, or carries no RETURNING and its outcome is read from cursor.rowcount — never both.
L898 | RULE | On a RETURNING statement, cursor.rowcount is 0 until the cursor is exhausted.
L898 | RULE | Reading rowcount on a RETURNING statement therefore reports "nothing happened" while the write has already landed.
L898 | BOUNDARY | That misreading is strictly worse than a no-op, because it hides a write that actually occurred.

L899 | RULE | Update functions assemble their SET clause from an updates/sets list.
L899 | RULE | SQLite silently accepts SET meta = ?, meta = ? and applies only the last assignment.
L899 | RULE | Two branches that each write the same column therefore destroy each other's work while returning a success-shaped result.
L899 | RULE | A column that more than one argument can affect must be accumulated into a single value first and appended to SET exactly once.
L899 | RULE | That accumulated value must be built by mutating one single object, never by re-reading the pre-update row per branch.
L899 | WHY-NARROW | A stale re-read loses data even after the duplicate-assignment bug is fixed, so both faults (duplicate assignment and stale re-read) have to be removed together.
L899 | IDENT | update_finding and update_requirement are the worked example of this rule (CB-16).
L899 | RULE | Their docstrings carry the ordering contract, and TestUpdateMetaComposition guards it in both test files.
L899 | RULE | A test guarding this must assert against the SQL template, not the executed statement.
L899 | WHY-NARROW | set_trace_callback reports parameters already expanded into the SQL text, so a guard reading it cannot distinguish a real duplicate assignment from the same text occurring inside a bound value — giving both false passes and false failures.
L899 | IDENT | The RecordingConnection subclass in each test file captures SQL templates by overriding execute().
L899 | MEASURED | This makes the assertion sql.count("meta = ?") == 1 exact rather than approximate.

L900 | RULE | A column settable at INSERT should be settable at UPDATE too, or else explicitly documented as immutable.
L900 | RULE | The two entities (findings, requirements) must be checked against each other, not each in isolation — that cross-check is the actual lesson of this rule.
L900 | HISTORY-LOADBEARING | severity was write-once on findings while the sibling column priority was already mutable on requirements (CB-17); the asymmetry was invisible from inside findings.py alone because nothing compared the two entities.
L900 | EXAMPLE | reported_at_commit is the worked example of the opposite branch: deliberately immutable, and documented as such in its docstring.
L900 | HISTORY-LOADBEARING | This bullet previously stated the rule was violated ("a target with a known outstanding debt", CB-21) because update_finding reached only status, severity, tags, meta, reported_at_ref while update_requirement could already rewrite description — the same asymmetry as CB-17 — and source was INSERT-settable on both entities but appeared in neither update contract.
L900 | RULE | Nothing anywhere stated the intended mutability matrix, so three independent inspection passes over the same function each found a different missing column.
L900 | RULE | General lesson: enumeration by inspection does not converge, and prose is the wrong enforcement layer for a defect whose defining property is invisibility from inside a single file.
L900 | IDENT | tests/test_update_parity.py is the enforcement layer for this rule.
L900 | RULE | For both entities that test reads PRAGMA table_info, the update_* function signature, the MCP wrapper's signature, and the CLI parser's argparse dests.
L900 | RULE | The test fails on any column that is neither declared MUTABLE (naming the parameters that write it) nor declared IMMUTABLE with a stated reason.
L900 | RULE | Consequently a new column, a new writing parameter, or a new surface argument turns the test red instead of waiting to be caught by a fourth manual inspection.
L900 | RULE | The residual findings cells were closed by DECLARATION rather than by widening update_finding to accept them.
L900 | RULE | description, category and file are the three inputs of the derived auto:v1 fingerprint, so making any of them a mutable UPDATE argument would turn update_finding into a re-key of identity.
L900 | RULE | Re-keying identity is a separate negotiated contract (CB-43 item 6); CB-61 negotiated exactly one such operation, normalize_categories, which issues its own UPDATE for precisely that reason.
L900 | HISTORY | This corrects the originating card's own premise, which recommended making file/description mutable on the grounds that "there is no integrity argument for freezing" them — there is one.
L900 | RULE | source carries BT-4's first-reporter reason for immutability on both entities.
L900 | RULE | A declaration in the parity gate is not a verdict: whether description/file should ever become mutable stays an OPEN question, stated as such in the IMMUTABLE docstring.
L900 | RULE | The gate's job is only to force that open question to be stated explicitly, rather than left to be rediscovered.
L900 | BOUNDARY | The CLI/MCP surface axis is declared but deliberately NOT closed: the CLI update verb lacks --tags/--meta-update/--reported-at-ref and reqs-update lacks --section/--tags/--meta-update, each recorded in SURFACE_GAPS with its own reason.
L900 | RULE | A hole declared in SURFACE_GAPS for an argument that in fact IS present also fails the gate, so the gap list cannot rot into silent permission to skip a surface forever.
L900 | IDENT | That surface-completeness question is tracked separately as CB-6.

L901 | HISTORY | This rule used to read "severity is exact-match on update, because that is what add_finding enforces."
L901 | RULE | CB-19 closed that gap: resolve_severity now runs on add, batch_add, update, CSV import, and the query filters, so severity normalizes identically to status everywhere.
L901 | RULE | General principle: match a field's own insert-time contract, not a neighbouring field's contract, and when unifying behaviour across sites, unify every site in one step.
L901 | WHY-NARROW | Making the update path lenient while the insert path stayed strict would have created a worse, same-field inconsistency, so the normalization seam had to move everywhere at once rather than site by site.
L901 | RULE | What normalization forgives is still spelling only, never meaning: severity has no aliases, so inputs like crit, P0, or sev1 raise rather than being coerced.

L902 | RULE | Never ORDER BY a vocabulary column directly, because it sorts alphabetically rather than by rank.
L902 | MEASURED | severity and priority are TEXT columns with a CHECK constraint, not ordered types, so ORDER BY severity yields critical, high, low, medium and ORDER BY priority yields could, must, should — the latter inverting the intended ranking outright.
L902 | RULE | Under a LIMIT this defect truncates the rows that actually matter rather than merely displaying them in a confusing order, and nothing signals that it happened (CB-20).
L902 | RULE | Use types.rank_case_sql(column, vocabulary), which derives the rank ordering from the vocabulary tuple so the SQL cannot drift out of sync with it, binds the values rather than interpolating them, and sends unknown values last.
L902 | RULE | rank_case_sql's parameters must be spliced at the fragment's exact textual position, not prepended — in query_findings that position is after the WHERE params and before LIMIT/OFFSET.
L902 | BOUNDARY | Getting that splice position wrong corrupts only FILTERED queries, so unfiltered tests would keep passing and hide the defect.
L902 | IDENT | blockers.query_deferred_entities orders by EntityKind.sort_col, whose ranking precedence is declared alongside it as sort_vocabulary; the two must be kept together.
L902 | RULE | findings.get_stats and reqs.get_reqs_stats are immune to this defect only because they pre-seed their output dict with the full vocabulary — that pre-seeding is load-bearing, not decoration.

L903 | RULE | A vocabulary must resolve on BOTH sides of an entity: the write path and the query filter path.
L903 | RULE | Normalizing writes while leaving a filter to compare raw text against the canonical column is worse than normalizing neither, because a caller can store a value and then be silently unable to find it again by the same spelling.
L903 | MEASURED | query_requirements(priority="SHOULD") returned zero rows for a row that update_requirement(priority="SHOULD") had just written normalized as "should" (CB-19) — "no requirements" is indistinguishable from an empty queue.
L903 | IDENT | The four resolvers live in types.py: resolve_severity, resolve_priority, resolve_finding_status, resolve_requirement_status, and every one of the five write sites and four filter sites routes through them.
L903 | RULE | _resolve is also where non-string input is refused, so passing None raises the documented ValueError contract instead of an AttributeError from calling .lower() on it.
L903 | WHY-NARROW | Guarding against non-string input per-resolver instead of centrally would leave the next resolver written to re-acquire the same hole.
L903 | BOUNDARY | An empty-string filter is still treated as "no filter" and is never validated against the vocabulary.
L903 | HISTORY | That empty-string-as-no-filter behaviour used to happen only because the `if severity:` guard short-circuited first (the CB-25 defect); now it happens because is_vocabulary_filter_active says so explicitly.
L903 | RULE | Normalization here still forgives spelling only, never meaning: severity has no aliases, so crit/P0/sev1 still raise, and adding aliases would need evidence of real callers needing them.

L904 | RULE | "No filter" means exactly None or the empty string "" — never decided by truthiness, and never decided with a != comparison.
L904 | RULE | A vocabulary filter guarded by `if severity:` conflates three distinct states: not supplied, the documented empty filter, and wrong input.
L904 | MEASURED | A falsey non-string value skipped that guard's condition entirely, so query_findings(severity=0) returned the WHOLE table (CB-25).
L904 | RULE | Consequence: an unfiltered queue becomes indistinguishable from a correctly filtered one, and the resolver — which could have caught the bad type — is never even reached.
L904 | IDENT | types.is_vocabulary_filter_active is the single canonical definition of "is this filter active."
L904 | RULE | The predicate must be type-based by design: deciding it with `value is not None and value != ""` reintroduces the very defect it fixes.
L904 | MEASURED | unittest.mock.ANY is truthy yet compares equal to "", and a str subclass overriding __ne__ can do the same trick against a valid value like "open" — so the predicate must never invoke equality or len() on the value itself.
L904 | IDENT | Both traps are pinned in tests/test_types.py::TestIsVocabularyFilterActive; without those two specific cases, the wrong predicate implementation passes every other test.
L904 | RULE | (1) is_vocabulary_filter_active is scoped to vocabulary filters only and must not be applied to ids/tags, where an empty list legitimately means "no filter."
L904 | BOUNDARY | An ACTIVE empty filter on ids/tags emits SQL like `id IN ()`, which is valid and returns zero rows — a silent empty queue replacing a silent full one, which is quieter and worse.
L904 | RULE | (2) A caller whose contract is "apply a default" rather than "no filter" still uses is_vocabulary_filter_active but keeps its own default value — provenance.check_findings maps None/"" to "open".
L904 | RULE | (3) The sweep for this defect found three MORE filters that validated their vocabulary on the write side only, never on the read/query side.
L904 | MEASURED | merge.get_sessions had types.MERGE_STATUSES defined but as dead code, so only the database CHECK constraint enforced it; milestones.list_milestones had MILESTONE_KINDS/MILESTONE_STATES defined but never consulted them on query; blockers.query_blockers had its TRIGGER_TYPES check placed INSIDE the truthy guard, so it was skipped entirely by any falsey value.
L904 | RULE | General method: sweep for the SHAPE of a defect, not for the specific names already known to be affected.
L904 | HISTORY-LOADBEARING | The first sweep grepped for `if status:|if severity:|if priority:|…` — an enumeration of already-known filter names — and therefore structurally could not find trigger_type; searching for the shape `if <name>:` wrapping a vocabulary check finds it in one grep instead.
L904 | REPEAT | Restates the document's recurring lesson (L904 itself, general form): a rule expressed as an enumeration is the letter, and the letter cannot decide.
L904 | BOUNDARY | Free-text filters are NOT fixed by this rule and remain tracked separately as CB-29.
L904 | BOUNDARY | A filter that gets silently discarded by ROUTING rather than by validation is a distinct, separately tracked defect, CB-28.

L905 | RULE | On a WRITE path, None is the only value meaning "not supplied" — and this is deliberately a DIFFERENT rule from the query-side one.
L905 | RULE | types.is_vocabulary_filter_active treats both None and "" as "no filter," which is correct for a filter because an absent filter matches everything.
L905 | RULE | A value being written to storage is the opposite case: "absent" there means "invent a default," so resolving that decision with truthiness lets a falsey value of the WRONG TYPE be silently replaced by a default.
L905 | MEASURED | bench.import_csv did exactly that with `date or utc_now()[:10]` and `run_id or _next_run_id(conn)`, so date=[], date={}, and date="" all silently stored today's date and reported success.
L905 | HISTORY | The originating card itself got this wrong, claiming the dict case ({}) would raise — {} is falsey in Python and took the same silent fallback path as the others.
L905 | RULE | Validate every non-payload argument BEFORE anything is parsed or written, so that a refusal costs no partial work.
L905 | RULE | Raise the ValueError the module's contract promises, rather than letting sqlite3.ProgrammingError or json.dumps' TypeError leak out to the caller.
L905 | RULE | Serialize a JSON container to a string exactly ONCE and store that exact string.
L905 | WHY-NARROW | Validating with one json.dumps() call and storing with a second call leaves a window in which a mutable or __iter__-overriding subclass could present different data to each call — this is CB-74's "validating one view while consuming another" recurring in a new location.
L905 | IDENT | This is pinned by a test whose list argument mutates between iterations (Codex diff review finding).
L905 | RULE | Check member types explicitly, because json.dumps silently accepts problems that later break other code: it writes [1, 2] for a tags list without complaint and silently coerces a non-string dict key to a string.
L905 | MEASURED | A non-string tag surviving this silent coercion later crashes bench-list's `",".join(tags)` call.
L905 | BOUNDARY | Rejected addition: the first draft of this fix added allow_nan=False, which would have refused meta={"x": nan} even though that value stores and round-trips fine today — an unrequested behaviour change that would have ridden along inside an unrelated validation fix.
L905 | BOUNDARY | The guard makes the downstream `x or default` fallback unreachable for genuinely bad input, so rewriting that fallback as `x is None` would be defence-in-depth that no test can currently discriminate — that should be stated honestly rather than claimed as "covered."

L906 | RULE | Findings have an identity function (CB-43): the add operation is an upsert, not a plain insert.
L906 | RULE | A FINDING represents a defect; an OCCURRENCE is one single observation of that defect.
L906 | RULE | Every observation is routed through a fingerprint computation.
L906 | RULE | A fingerprint hit on a LIVE row (status open/in_progress/stale) bumps occurrence_count and returns that existing row, reporting was_new: False.
L906 | RULE | A fingerprint hit on a fixed row REOPENS it as a regression, and this fires the status-change hook.
L906 | IDENT | milestones.reconcile._reconcile_on_reopen is what reopens the corresponding stream item on such a regression.
L906 | WHY-NARROW | This reopen-routing exists because the terminal reconciler otherwise early-returns on a nonterminal transition and the add-side router uses INSERT OR IGNORE — without the explicit reopen call, a reopened card would be invisible to every queue, which is strictly worse than merely creating a duplicate.
L906 | RULE | A fingerprint hit on a wont_fix or not_a_bug row instead files a brand-new row carrying meta.recurrence_of, because a decision, once made, stays decided.
L906 | RULE | (1) The dedup branch table must be TOTAL over the full FINDING_STATUSES vocabulary.
L906 | IDENT | tests/test_dedup.py::TestBranchTotality pins that totality.
L906 | BOUNDARY | An unclassified status value would silently resume the exact duplicate-explosion problem this system exists to prevent.
L906 | RULE | (2) "At most one live row per fingerprint" is enforced as a partial unique index, ux_findings_fingerprint_live.
L906 | RULE | That index is declared in _POST_MIGRATION_INDEXES and deliberately NEVER inside the SCHEMA constant.
L906 | WHY-NARROW | SCHEMA runs before _migrate_statuses' hardcoded table rebuild, which would either crash on a missing column or silently drop the index if the index lived in SCHEMA instead.
L906 | RULE | (3) Supplying an explicit finding_id is treated as an assertion of identity, and it BYPASSES both fingerprint derivation and fingerprint matching entirely.
L906 | MEASURED | 158 of 173 test call sites create their fixtures from identical description tuples relying on this bypass.
L906 | RULE | A test helper that needs N genuinely distinct entities must therefore vary its default description text.
L906 | RULE | (4) The auto:v1: fallback fingerprint hashes a canonical JSON ARRAY, never a joined string, because separators inside free text are ambiguous.
L906 | RULE | That array is built from category, file, and a normalized description — normalized by stripping the observation's OWN meta string values, then ISO timestamps (stripped BEFORE lowercasing, because the timestamp pattern anchors on literal T/Z), then digit-bearing hex runs.
L906 | RULE | General numeric values are deliberately kept in the hash input, because e.g. rc=124 versus rc=1 represents a real, meaningful family split.
L906 | MEASURED | On the motivating corpus family, measured duplicate collapse was 0 out of 115 without meta-stripping and 71 out of 115 with meta-stripping enabled.
L906 | RULE | (5) update_finding pre-checks any terminal-to-live status transition against the unique index and raises a domain ValueError naming the specific blocking row.
L906 | IDENT | db.is_contention matches SQLite result codes {5,6}, and SQLITE_CONSTRAINT is code 19, so a leaked raw IntegrityError from this path would be unclassifiable everywhere else in the codebase.
L906 | RULE | (6) fingerprint is settable at INSERT time, and update_finding still documents it as immutable at update time.
L906 | HISTORY | The re-keying operation this clause once deferred to "a future card" HAS since been negotiated as CB-61.
L906 | RULE | That relaxation is declared in exactly the ONE function that received it, rather than generalized: findings.normalize_categories (exposed as MCP tool categories_normalize, CLI categories-normalize) is the single sanctioned re-key operation.
L906 | RULE | normalize_categories issues its own UPDATE statement rather than routing through the general updater, so no other caller can acquire a re-key capability merely by passing an argument.
L906 | RULE | normalize_categories is DRY RUN BY DEFAULT: no write transaction is opened at all unless apply=True (CLI: --apply) is passed.
L906 | RULE | Only the auto:v1 hash is ever re-derived, only from the CATEGORY input, and only using the SAME normalizer version, so that a NULL stored hash and a caller-SUPPLIED hash stay byte-identical after the operation.
L906 | RULE | A row whose stored inputs no longer reproduce its own stored hash is skipped WHOLE — its category is not rewritten either — and is reported as "unverifiable".
L906 | RULE | A fold that would place two live rows onto the same fingerprint is REPORT-AND-STOP: the run writes nothing and names the colliding pairs, because an automatic merge would have to invent a winner between them.
L906 | RULE | (7) An import is not an observation, and findings.import_findings — deliberately not the CLI handler — is where that principle is actually enforced (CB-51).
L906 | HISTORY-LOADBEARING | The rule used to read simply "CSV import skips rows whose exported id already exists," and that guard was proven both too strong and too weak.
L906 | MEASURED | Too weak: the check was bare-id EXISTENCE, not true identity, so a foreign row whose id merely did not collide walked past it and REOPENED a local fixed card via fingerprint matching — measured, a peer tracker's CB-9001 flipped a local CB-1 from fixed back to open.
L906 | MEASURED | Too strong: every tracker independently numbers its rows CB-1, CB-2, …, so a foreign export lost every row whose NUMBER happened to already be taken locally — measured, importing 3 peer rows into a 3-row tracker landed 0 rows, reported as "3 already present."
L906 | MEASURED | Because export-csv orders rows by SEVERITY rather than by id, ids MINTED BY THE IMPORT ITSELF collided with later rows in the same import file, so restoring a backup into an EMPTY tracker silently dropped rows — measured, 3 rows exported, only 2 restored, at exit 0.
L906 | RULE | Fixed behaviour: a fingerprint hit on a row with a reopen-eligible status is now SKIPPED during import (a live-row hit still bumps as normal; a wont_fix hit still files a recurrence as normal — neither of those was itself a defect).
L906 | RULE | The id-collision guard now compares CONTENT as well as raw id, so a colliding foreign row lands under a freshly minted local id, tagged with meta.imported_id.
L906 | BOUNDARY | The id-based half of the guard cannot simply be deleted: a row written with an explicit id stores fingerprint = NULL, and NULL matches nothing in SQL, so a fingerprint-only skip check cannot see it — every pre-CB-43 row and every explicit-id row would duplicate on each re-import without the id check.
L906 | RULE | The entire import loop runs inside ONE db.txn (CB-77), so a read failure lands nothing at all, and the rollback path must not print any partial count.
L906 | BOUNDARY | batch_add_findings is deliberately NOT used as the import seam: it has no annotate parameter (so import would silently run the similarity resolver per row while holding the write lock), and it validates every member before opening its transaction, which forbids partitioning errors per row.
L906 | BOUNDARY | A faithful backup RESTORE that preserves id, status, occurrence_count, and created_at verbatim is a different seam entirely — a raw INSERT bypassing identity, resolvers, and post-add hooks — tracked separately as CB-97, not this rule.
L906 | RULE | (8) Severity is MONOTONIC under observation, and it is the only column a bump ever refreshes (CB-52).
L906 | HISTORY-LOADBEARING | Dedup used to freeze every column at first report while the newest evidence lived only in the occurrence ring: a card filed as low and later re-observed as critical stayed low forever and was invisible to query(severity="critical"), the primary read path.
L906 | RULE | _bump_row now writes the MORE SEVERE of (stored, observed) — escalation only — so a critical card later re-observed as low stays critical; use update_finding to downgrade deliberately if needed.
L906 | RULE | The severity comparison goes through types.severity_rank, derived from the SEVERITIES tuple the same way rank_case_sql is, because a second hand-written precedence table would be one drift away from disagreeing with the first (CB-22).
L906 | RULE | The direction is a trap: SEVERITIES is ordered most-severe-first, so "the worse of two" is computed as min() over ranks, and using max() would be backwards under either ordering convention.
L906 | RULE | escalate=False has exactly one call site in the whole codebase: import_findings.
L906 | WHY-NARROW | An import is not an observation (CB-51), so a peer tracker's CSV records the occurrence count but must not re-rate a local card's severity based on foreign evidence.
L906 | RULE | The escalate=False call sits one line below the analogous annotate=False call, present for the identical reason.
L906 | RULE | escalate is deliberately NOT exposed as a parameter on add_finding (unlike annotate, which is), so no MCP or CLI caller can turn the escalation invariant off via argument.
L906 | IDENT | The single-call-site count is pinned by tests/test_dedup.py::TestEscalateOptOutRatchet, which reads the source by AST rather than by grep.
L906 | HISTORY-LOADBEARING | Every other count in this document is meant to be held by a test rather than stated in prose, because prose counts have twice already been proven stale ("three copies" was actually four; CB-24's originally-cited four sites were actually nineteen).
L906 | HISTORY-LOADBEARING | The parameter-ordering hazard CB-52 created was closed structurally rather than merely documented: `meta = ?` used to be spliced OUTSIDE the built `sets` clause, with its parameter appended after the builder finished — harmless only while `status = 'open'` (a literal consuming no bound parameter) was the sole other addition.
L906 | RULE | CB-52 added the first PARAMETER-CONSUMING extension to that pattern, so meta was moved INTO the builder, and every fragment is now appended together with its own parameter in lockstep.
L906 | REPEAT | Restates the document's "point-of-use discipline is the wrong enforcement layer" lesson (CB-41), applied here to SET-clause construction.
L906 | MEASURED | One extra SET-clause column ended up needing four separate prose warnings before being fixed structurally — used as evidence that point-of-use discipline does not scale.
L906 | BOUNDARY | Generalizing this fix across the package's seven other string-built SET clauses is CB-37's open question, not resolved by this card.
L906 | BOUNDARY | Two things this rule does NOT do, both deliberate: reported_at_commit stays frozen even under escalation (CB-53's separate, already-answered question — readers consult the occurrence ring instead); and milestone routing is NOT re-evaluated on escalation.
L906 | BOUNDARY | stream/security placement is still decided once, at filing time only — that re-routing question is CB-35, left open.
L906 | MEASURED | The measurement that keeps CB-35 out of active scope: stream/security has held total_items: 0 for the entire life of this tracker, so the routing symptom CB-35 worries about has never once actually occurred.
L906 | RULE | (9) TAGS UNION under observation (BT-4): a bump merges the new observation's tags into the stored tags column, on BOTH live bumps and reopen bumps, because a regression is itself a fresh observation.
L906 | RULE | The tag union uses exact string equality with no casefolding — "Tag" and "tag" both survive as distinct live tags.
L906 | RULE | Union order is first-encountered, with previously-stored tags placed before newly observed tags, and the result is deduplicated.
L906 | RULE | The merged tag container is json.dumps'ed exactly ONCE, and that exact resulting string is the bound SQL parameter (CB-82).
L906 | RULE | `tags = ?` is appended INSIDE the sets builder, paired with its own parameter, exactly once (CB-16).
L906 | RULE | promote_tags=False has exactly one call site in the codebase: import_findings.
L906 | WHY-NARROW | An import is not an observation (CB-51), so foreign tags are kept out of the local tags column even though the occurrence ring still records them.
L906 | IDENT | This single-call-site count is pinned by tests/test_dedup.py::TestPromoteTagsOptOutRatchet, an AST-based test of the same shape as the escalate ratchet.
L906 | RULE | promote_tags is deliberately absent from the add_finding/batch_add_findings function signatures, so no MCP or CLI caller can disable the tag union via argument.
L906 | RULE | Stored tags are STRICT-parsed before any write, but only on the promote path: the union cannot be computed from a stored value that fails to parse, so a bump attempted over malformed stored tags now fails cleanly with nothing landed.
L906 | HISTORY-LOADBEARING | This change MOVED the malformed-stored-tags corruption class from a post-commit failure (PostCommitCorruptionError, which remains as a defensive classifier with an honestly-stated reachability note) to a pre-write json.JSONDecodeError.
L906 | BOUNDARY | On the import path specifically, the tags column is neither read nor written, so an import's live-fingerprint-hit on an already-corrupt row still lands successfully.
L906 | RULE | A valid but non-list stored tags value is DISPLACED rather than merged, following the occurrence-ring guard's own convention, and never raises a TypeError.
L906 | RULE | The manual update_finding update path acquired NO tag-union behaviour at all — it never calls _bump_row — and a pin test confirms a plain status write leaves the tags column untouched.
L906 | BOUNDARY | Tag REMOVAL is deliberately not built: update_finding(tags=) stays a full replace operation, so a hand-removed tag reappears with the very next observation that carries it; the sub-decision of how to fix this (a cap, tombstones, or a separate finding_tags table) is left OPEN with the owner.
L906 | RULE | (10) Three MORE fields are observation-frozen, now DECLARED IN WORDS explicitly (BT-4, ratified 2026-08-20) — the underlying behaviour was unchanged, only newly documented on every reader (T-11).
L906 | RULE | source is frozen as the FIRST reporter by design: later observations' sources live only in the occurrence ring, and an imported observation's ring-recorded source can legitimately be a peer tracker's own identifier.
L906 | RULE | This closes CB-21's outstanding "source" cell as a formally DECLARED immutability, and tests/test_update_parity.py now carries source in its IMMUTABLE set with this stated reason, for both entities.
L906 | RULE | reported_at_ref is observation-frozen but remains manually mutable BY DESIGN via update_finding(reported_at_ref=), because a release is typically tagged after the finding was originally filed — this must not be confused with the separately immutable reported_at_commit.
L906 | RULE | query(ref=) matches the first-observed-or-manually-assigned ref exactly, and never consults the occurrence ring — no ring-reading implementation exists yet because no consumer of "latest observation" semantics has appeared.
L906 | RULE | Top-level meta represents the row's AUTHORED state: a re-observation's own meta lands only as per-occurrence evidence inside meta.occurrences[*].meta, never merged upward.
L906 | RULE | query(meta_key=)/meta_value read only the authored top-level meta column; promoting specific occurrence keys up into the row is left as a future allowlist feature, gated on measured demand, and deliberately not a general automatic merge.
L906 | IDENT | This is pinned by tests/test_boundary.py::TestBt4FreshnessDeclarations (checks prose matches code) and tests/test_dedup.py::TestObservationFrozenFields (checks actual behaviour).
L906 | RULE | (11) The attention response block: a serious divergence between an incoming observation and the existing card it matched is surfaced as a STRUCTURAL, top-level response field rather than something a caller must dig out of the response body (BT-5, ratified 2026-08-20).
L906 | RULE | The attention key is present in EVERY add/batch_add response, across all four dedup branches, and an empty list [] is a normal, valid answer meaning "evaluated, nothing fired" — it must never be read as "no such channel exists."
L906 | IDENT | The precedent for this unconditional-key discipline is claims._response, all of whose keys are unconditional, plus the two response-only keys already sitting beside attention.
L906 | RULE | The attention signal vocabulary is CLOSED, and the mapping of signal to which dedup branches can emit it is DERIVED and LIVE, not a static list.
L906 | IDENT | _ATTENTION_SIGNALS_BY_ACTION is read directly by the response builder, so an incorrect cell in that table changes the actual wire RESPONSE, not merely a test's expectation.
L906 | RULE | An unclassified dedup action raises KeyError — fail-closed — because "evaluated, nothing fired" is the one meaning that a newly added dedup branch must never be allowed to borrow by accident.
L906 | RULE | Two attention signals exist today: severity_escalated (carrying from/to, emitted on the bumped/reopened branches) and category_divergence (carrying observed/stored, emitted on every branch that has a matched row, including recurrence_of_closed, where the comparison target is the dismissed twin row).
L906 | RULE | Both sides of a category_divergence comparison are normalized, so a difference of spelling alone is not treated as a signal while a difference of actual name is; a non-string stored category value is skipped from the comparison, for the same reason _existing_categories skips one.
L906 | IDENT | The internal transport for this machinery is AddOutcome/BumpOutcome.
L906 | RULE | The single severity comparison used for escalation logic must stay confined inside _bump_row — a second, independent copy of that comparison appearing anywhere else is exactly the kind of drift CB-41/CB-52 exist to prevent.
L906 | RULE | Attention signals are assembled INSIDE the database transaction, so that _finalize_add remains purely MECHANICAL — the post-commit response-conversion path must never acquire a new way to fail.
L906 | RULE | Import carries no attention block AT ALL, by construction, because import_findings reads the dedup outcome directly and never calls _finalize_add — there is therefore no opt-out flag needed, since one would be dead code.
L906 | BOUNDARY | The attention feature's audience is MCP-only: the CLI prints fixed text lines and never serializes the structured response, and there is no batch verb on the CLI at all.
L906 | BOUNDARY | The wire golden schema file is NOT the gate on this response shape: no outputSchema is snapshotted for it, and the live schema declares additionalProperties: True, so widening the response would be a change a golden-schema gate could never catch — the real gate is a behavioural MCP-result test instead.
L906 | BOUNDARY | Exact numbers (how many signal types, how many response cells) live only in the tests and are deliberately not restated here, to avoid the stale-count problem this document has hit before.
L906 | RULE | (12) STRIP WITH VISIBILITY, on the ADD path only (CB-56/CB-60, ratified 2026-08-24 as T-59, closed by the wire pin CB-160).
L906 | RULE | add/batch_add no longer outright REFUSE a caller-supplied meta key that happens to be identity machinery's own reserved OUTPUT key (occurrences, occurrences_dropped, regressed, recurrence_of, category_minted, fingerprint_refusals, plus any extension's own reserved keys obtained via db.resolver_reserved_meta_keys()).
L906 | WHY-NARROW | This relaxation exists because a get → modify → add round trip by a caller is a realistic usage shape, and CSV import had already handled this identical situation by silently stripping rather than refusing.
L906 | RULE | What gets stripped IS reported, never silent: stripped_meta_keys follows the exact same discipline as the attention field — an unconditional top-level list on every branch, where [] means "checked, nothing needed stripping" and never "no such channel."
L906 | WHY-NARROW | A caller must be able to tell, from the response alone, which of its own submitted keys silently failed to land — this is the CB-15 "discarded caller data" failure shape, applied here to a silent strip exactly as much as to a silent outright refusal.
L906 | RULE | resolver_errors is the ONE reserved key that remains an outright REFUSAL rather than being merely stripped, because it reports a FAILURE state (a resolver's own annotation attempt did not land) rather than being ordinary machinery input.
L906 | WHY-NARROW | Silently discarding a caller's belief that "my last observation's resolver failed" is exactly the harm that stripping-with-visibility exists to prevent for every other key.
L906 | RULE | The UPDATE path is untouched by this change: update's meta_update parameter still outright refuses every reserved key, with the sole exception of a resolver-declared UPDATABLE key such as similar_to (sourced from the extension registry, never from a hardcoded literal).
L906 | WHY-NARROW | An unrepairable machinery stamp surviving under a silent strip on the UPDATE path would recreate the CB-26 failure shape.
L906 | BOUNDARY | One divergence is named rather than claimed closed: CSV import strips the same dynamic reserved-key union, INCLUDING resolver_errors, silently and with no response key at all, because import is not an observation (CB-51) and that particular contract was ratified as a separate decision.
L906 | BOUNDARY | So "one uniform behaviour across every ingestion surface" is explicitly NOT what this change achieves; the remaining divergence is narrowed to exactly one key (resolver_errors: refused on add, silently stripped on import) and is pinned by a test rather than reconciled away.
L906 | RULE | Both the add and batch_add MCP tool descriptions now name stripped_meta_keys explicitly, and the wire golden schema file was updated to match.
L906 | WHY-NARROW | Updating the tool description text is legitimate here because a tool description is an INPUT that shapes the schema, not itself the gated response shape.
L906 | RULE | (13) A third opt-out of the same family, and the one whose effect is to refuse a COMBINATION rather than to narrow a merge (CB-230).
L906 | RULE | What makes escalate=False, promote_tags=False, and authored=False a family: each is a keyword-only opt-out that disables exactly one write this package performs on its own initiative, each has exactly one call site, each is absent from every external surface, and each has an AST ratchet test pinning its call-site count.
L906 | HISTORY-LOADBEARING | The family's defining clause used to read "one observation-time invariant," which was actually wrong about the very member being introduced (CB-247): escalate and promote_tags do sit on the observation (add) path, but authored sits on the UPDATE path instead.
L906 | HISTORY-LOADBEARING | The same item then contradicted that opening clause in its own later words, both by calling authored a SERVICE write and by explicitly contrasting it against its two siblings — so the paragraph disagreed with itself without ever leaving its own bullet point.
L906 | RULE | The PATH each opt-out sits on (add vs. update) is where the three genuinely differ, and that difference is the actual subject of the rest of this item rather than something the family's defining clause should have flattened away.
L906 | RULE | update_finding(..., authored=False) is a SERVICE write governing EXACTLY ONE database column.
L906 | RULE | Under authored=False, the `updated_at = ?` assignment is simply not appended to the built SET clause; every other step below that check — the id parameter, the UPDATE execution itself, hook firing, and the re-read — is deliberately left OUTSIDE that conditional.
L906 | RULE | Effect: the authored flag changes only whether the row claims a human recently touched it, and never changes what data actually lands.
L906 | RULE | authored=False has exactly one call site in the codebase: loc.py's anchor-refresh logic.
L906 | WHY-NARROW | A code anchor is this module's own derived output, not something a person or a commit wrote, so refreshing an anchor's position must not make the card falsely appear as recently, humanly changed.
L906 | IDENT | This single-call-site count is pinned by tests/test_cb230_service_write.py::TestServiceWriteCallSiteRatchet, of the same shape as the escalate and promote-tags ratchets.
L906 | RULE | authored is deliberately absent from both the MCP tool surface and the CLI surface, and the same ratchet test asserts this against the MCP wrapper's function SIGNATURE and the CLI's argparse DESTS directly — not against any prose text.
L906 | BOUNDARY | This absence must NOT be re-checked by grepping the wire golden schema files: tests/golden/mcp_schema.json legitimately contains the literal word "authored" once, as ordinary prose inside query's tool description (explaining the authored-versus-ring meta distinction), so a raw text search answers a different question than the one intended.
L906 | MEASURED | Measured 2026-08-28: this prose occurrence defeated the first draft of this very sentence in the document, which had wrongly claimed zero occurrences of "authored" in either golden file.
L906 | RULE | Where authored=False differs from its two siblings: escalate=False and promote_tags=False each NARROW what an automatic merge does, whereas authored=False makes an entire COMBINATION of inputs unrepresentable.
L906 | RULE | Passing status= together with authored=False is REFUSED outright, and that refusal happens ABOVE the database transaction, so nothing is ever written before the refusal fires.
L906 | RULE | Rationale: a status change is inherently an authored act — a person or a commit closed the card — so the one scenario in which this flag could ever have erased a real "last touched" date is foreclosed by construction rather than merely by discipline at the point of use.
L906 | REPEAT | This is CB-41's "make the bad state unrepresentable rather than rely on point-of-use discipline" rule, restated here as applied to a boolean flag instead of to a timestamp deadline.

L908 | RULE | Category spelling is normalized, and MINTING a brand-new category name is gated — but only on the OBSERVATION (add) path (CB-60).
L908 | RULE | types.normalize_category (casefold, strip, collapse hyphen/whitespace runs to underscore) runs inside add_finding/batch_add_findings exactly when finding_id is None — the same predicate used by dedup and by the pre-add resolvers — and it runs BEFORE auto:v1 fingerprint derivation.
L908 | MEASURED | This closes a measured identity fork where twin spellings like process-improvement and process_improvement used to hash and store as two different categories; now they hash and store as one canonical name.
L908 | RULE | A category the table does not already hold — compared on NORMALIZED forms, so a pre-CB-60 stored spelling still legitimizes its normalized twin — requires new_category=True (available on both domain functions, on the MCP add/batch_add tools, and as CLI flag --new-category).
L908 | RULE | Without new_category=True, a near-miss spelling is refused with a message naming the canonical existing spelling; the near-miss detector is Levenshtein distance with a conservative length-scaled threshold.
L908 | RULE | The new_category flag escapes either kind of refusal regardless of distance, so the Levenshtein threshold only shapes the wording of the refusal message and never actually blocks a caller determined to mint a new category.
L908 | RULE | A genuinely new category name (no near match found) is refused with a message listing the nearest existing category names.
L908 | RULE | A permitted mint stamps meta.category_minted: true on the row, so query(meta_key="category_minted") can be used to count minting events across the tracker.
L908 | RULE | The category_minted stamp is reserved on ADD only, making it spoof-proof, and is deliberately still writable via update_finding(meta_update=) — because an unrepairable stamp is exactly the CB-26 failure shape.
L908 | RULE | The empty string "" remains a legal, entirely ungated category value, and similarity's pooling logic matches it exactly.
L908 | RULE | Three paths are deliberately NOT normalized and NOT gated: explicit-finding_id adds (identity is asserted, so fixtures may file category text verbatim), import_findings (governed by CB-51's verbatim-preservation contract; a foreign category_minted key is stripped like any other reserved key), and restore_findings (a raw INSERT bypassing all of this machinery).
L908 | RULE | The ADD path never rewrites an already-stored row's category — that half of the behaviour is unchanged, and it is exactly why an old-spelling row does not fingerprint-match a newly-normalized incoming observation.
L908 | HISTORY | The sentence that used to follow this, describing the retro-fold as "deliberately left open," is no longer true: the retro-fold now EXISTS as a separate, explicit operation (findings.normalize_categories, CB-61) rather than as something the add path itself acquired.
L908 | RULE | Running normalize_categories against a live tracker is explicitly the OWNER's decision, not an automatic consequence of this code landing — it runs dry-run by default, and --apply is the owner's own choice to type.
L908 | MEASURED | This tracker's own corpus was folded exactly once (17 rows affected, no collisions, nothing left unverifiable), which is the reason it no longer carries variant spellings today.
L908 | BOUNDARY | A tracker that has never had normalize_categories run against it still carries variant category spellings.

L909 | RULE | Requirements deliberately have NO identity/dedup function at all — this was explicitly DECIDED on CB-45, whose card text delegated exactly that decision ("decide … or documents why not").
L909 | RULE | Rationale: requirement rows are authored artifacts with caller-assigned ids on every write path — the same explicit-id bypass that skips dedup machinery for findings — no automated filer emits requirement observations, and requirements similarity already exists separately via embeddings, so a fingerprint column with zero writers would simply be dead code.
L909 | BOUNDARY | Named revisit trigger: this decision should be reopened if an automated requirements filer ever appears.

L910 | RULE | Similarity extension (CB-45): similarity.py is the package's FIRST self-registering non-domain module, and this is legal specifically because it issues ZERO raw SQL of its own.
L910 | RULE | All row access from similarity.py goes through the public accessor findings.similarity_candidates, which returns raw rows with meta_json kept as the stored STRING (per CB-24 consequence 4) and a deterministic ORDER BY created_at, id.
L910 | RULE | No module other than findings.py itself may SELECT directly from the findings table.
L910 | RULE | The similarity detector is character-trigram Jaccard similarity computed over similarity.normalize_text, which equals the fingerprint normalization (exposed via the public wrapper findings.normalized_identity_text) plus an additional ANSI-remnant strip.
L910 | WHY-NARROW | That extra ANSI-stripping cleanup step lives in the extension module rather than in the core normalizer because auto:v1 is a versioned format and must not be allowed to drift.
L910 | RULE | DEFAULT_THRESHOLD = 0.7 was empirically CALIBRATED, not arbitrarily chosen.
L910 | MEASURED | On the 3162-row autosorter corpus, threshold 0.7 collapses 102 rows into 11 coherent families and splits the 115-row "gate" category into roughly 10 genuinely distinct failure tails.
L910 | HISTORY | The originating CB-45 card had proposed threshold 0.95; that value was measured and REJECTED (it produced 77 rows and the target family never unified — forcing that unification would itself be the false-merge outcome CB-43's RISK section forbids), and the rejection was communicated per the letter-fix notification protocol.
L910 | IDENT | tests/manual/verify_similarity_corpus.py reproduces these calibration numbers exactly.
L910 | RULE | MIN_TEXT_LEN = 40 lives at the SCORING layer, shared by the resolver, the report, and the check functions as one single policy.
L910 | MEASURED | Trigram Jaccard scores "Bug 1" versus "Bug 2" at approximately 0.8 similarity, and scores two empty strings at 1.0 similarity — the motivation for the MIN_TEXT_LEN floor.
L910 | RULE | The file-time resolver stamps meta.similar_to = [{id, score, status}, ...] drawn from a pool of live rows UNION {wont_fix, not_a_bug} rows within the same category.
L910 | WHY-NARROW | A "resembles CB-N, already dismissed" annotation is judged the most valuable kind of link; fixed rows are deliberately excluded from the pool because exact matches already trigger the reopen path instead.
L910 | RULE | The candidate pool is capped at the newest 500 rows, and trigram computations are memoized BY CONTENT rather than by row identity.
L910 | WHY-NARROW | Memoizing by an (id, created_at) key instead would collide across different databases whenever two rows share a whole-second timestamp.
L910 | RULE | The pool's category filter treats the empty string as a real VALUE to match, not as "no filter": findings permit category="", and the accessor's normal category= convention treats "" as meaning no filter is applied.
L910 | RULE | To match empty-category rows correctly, the resolver therefore passes the explicit-tuple form categories=("",) rather than category="".
L910 | WHY-NARROW | Without that explicit-tuple form, every empty-category observation would end up pooling the entire findings table (a Codex diff review finding); the same review round also replaced group_report's bare `status == "all"` sentinel comparison with a type-pinned check, closing CB-25's mock.ANY trap in a second location.
L910 | RULE | similar_to is reserved on the ADD path only, and is writable via update_finding(meta_update=) — an unrepairable annotation would be the CB-26 failure shape; resolver_errors, by contrast, is refused on BOTH the add and update paths.
L910 | RULE | The update-side exemption for similar_to is DECLARED at extension registration time (updatable_keys=("similar_to",)) and is read back from the registry via db.resolver_updatable_meta_keys() — it is never hardcoded inside findings.py itself.
L910 | WHY-NARROW | Core (findings.py) must not know an extension module's specific key names — this constraint came out of same-day code review.
L910 | RULE | The annotation pool's status set is likewise DERIVED, as LIVE_STATUSES + RECURRENCE_STATUSES, both exported publicly from findings, so TestBranchTotality's classification guarantee automatically extends to cover the similarity pool instead of relying on a separately re-spelled enumeration.
L910 | RULE | group_report (exposed as MCP tool similarity_report, CLI similarity-report) is CB-46's dry-run reporting function, and it surfaces its own supporting evidence.
L910 | RULE | Per-family min_pair_score is computed as the DIAMETER over ALL member pairs in the family, including sub-threshold pairs.
L910 | WHY-NARROW | Recorded edges are, by construction, always at or above the similarity threshold, so edges alone can never reveal the underlying union-find chaining that connected a family together.
L910 | MEASURED | The corpus's largest family (43 rows) hides a pair scoring only 0.392 similarity, connected only indirectly through a chain of edges each individually scoring 0.7 or above.
L910 | RULE | group_report also returns the underlying edge list plus excerpts of each member's description text.
L910 | RULE | The default population for group_report is LIVE rows only; passing status="all" widens that population.
L910 | WHY-NARROW | Grouping already-decided (terminal) rows into a merge-style dry run would contradict this codebase's "a decision stays decided" principle.
L910 | RULE | Embedding vectors passed to group_report are entirely caller-supplied and OFFLINE-only, via group_report(vectors=).
L910 | WHY-NARROW | An MCP client cannot practically transmit thousands of embedding vectors in a single tool call, which is why this path is offline-only rather than a live MCP parameter.

L911 | RULE | The one sanctioned cross-table status-write function in the entire codebase is entities.EntityRef.set_status(conn, new_status=…, expected=…).
L911 | RULE | set_status runs inside the CALLER's existing transaction and must never commit on its own; it returns only whether the target row actually moved.
L911 | RULE | Domain modules continue to own their own tables exclusively; set_status exists specifically so the claims ledger can project a status onto another entity without importing that entity's whole domain module.

L912 | RULE | An interpolated SQL identifier must be validated at the point where it is DECLARED, not at the point where it is used — types.is_sql_identifier is the only implementation of that validation pattern.
L912 | RULE | Bound VALUES are always parameterized; identifiers (table names, column names, ORDER BY targets) sometimes cannot be, since SQL parameter binding does not cover identifiers.
L912 | BOUNDARY | Only SOME identifier-interpolation sites in the codebase carry a `# noqa: S608` comment — most do not, including bench.py's run-listing query and blockers.py's trigger-type query — and there is no inventory anywhere recording which sites got a marker and which did not.
L912 | RULE | That # noqa: S608 marker checks nothing at all today, and this is stated as a real fact, not an aside: S608 is simply not among this repository's enabled ruff lint rules.
L912 | MEASURED | pyproject.toml carries no [tool.ruff.lint] section at all, so `ruff check` runs only its default rule selection and never evaluates S608 in the first place, meaning every existing `# noqa: S608` comment suppresses a warning the linter was never going to raise anyway.
L912 | RULE | The actual protection against SQL-identifier injection is validation at the identifier's point of DECLARATION, specifically EntityKind.__post_init__ and types.is_sql_identifier itself — not the linter.
L912 | RULE | Actually enabling the S608 lint rule is treated as a separate, deliberately accepted piece of debt, tracked as CB-172, carrying a real measured cost rather than being a free win.
L912 | MEASURED | Enabling S608 would immediately surface a batch of unsuppressed hits spread across many source files at once — this would be the project's first-ever [tool.ruff.lint] configuration — together with a comparable batch of dead noqa markers belonging to OTHER, unrelated rules that RUF100 would surface the moment any lint configuration exists.
L912 | RULE | EntityKind.__post_init__ validates table, sort_col, AND every member of readable_cols, so that a malformed EntityKind dies immediately at construction time.
L912 | RULE | This validation applies even when an EntityKind is constructed via dataclasses.replace(), which the test suite itself uses.
L912 | HISTORY-LOADBEARING | Before CB-22, a code comment falsely claimed all three fields were guarded, when in fact only sort_col was actually checked, and only inside order_by(); an EntityKind carrying readable_cols={"(SELECT meta FROM findings)"} passed the (incomplete) membership check and field() then happily returned the meta column to the caller.
L912 | RULE | General principle: an allowlist MEMBERSHIP check protects only the caller's argument against the allowlist — it says nothing about whether the allowlist's OWN contents are themselves safe; these are two entirely separate obligations, and only the first is visible from the query call site.
L912 | RULE | Anchor identifier-matching regexes with re.fullmatch, never with a hand-written `^…$` pattern.
L912 | MEASURED | `$` in a Python regex also matches immediately before a trailing newline character, so the old `^…$` pattern incorrectly accepted the string "findings\n" as a valid identifier.
L912 | RULE | General principle: a validation check that is DUPLICATED rather than genuinely SHARED between two locations is exactly one accidental drift away from silently disagreeing with itself.
L912 | HISTORY-LOADBEARING | entities._SAFE_IDENT and types._IDENT used to be two textually byte-identical regex patterns, and they happened to compile to the very same Python object only because Python's `re` module caches compiled patterns keyed on the source pattern string — a fragile accident, not a guarantee.
L912 | EXAMPLE | The same "identifier composed from an unchecked value" shape recurs in milestones/capacity.py, which builds a column name as f"{size}_held" and routes it through the shared helper _held_col().
L912 | MEASURED | Before that helper existed, an unknown size value raised OperationalError if the calling agent already had a capacity row, but silently lost the intended increment while still returning a success response if the agent had no such row yet — two different failure behaviours for the same underlying bug, one loud and one silent.

## VERBATIM-CRITICAL

L898 | RETURNING
L898 | cursor.rowcount
L899 | SET meta = ?, meta = ?
L899 | update_finding
L899 | update_requirement
L899 | CB-16
L899 | TestUpdateMetaComposition
L899 | set_trace_callback
L899 | RecordingConnection
L899 | sql.count("meta = ?") == 1
L900 | CB-17
L900 | reported_at_commit
L900 | CB-21
L900 | status, severity, tags, meta, reported_at_ref
L900 | description
L900 | source
L900 | tests/test_update_parity.py
L900 | PRAGMA table_info
L900 | MUTABLE
L900 | IMMUTABLE
L900 | auto:v1
L900 | CB-43
L900 | CB-61
L900 | normalize_categories
L900 | file
L900 | category
L900 | SURFACE_GAPS
L900 | --tags
L900 | --meta-update
L900 | --reported-at-ref
L900 | --section
L900 | reqs-update
L900 | CB-6
L901 | CB-19
L901 | resolve_severity
L901 | add
L901 | batch_add
L901 | update
L902 | CB-20
L902 | types.rank_case_sql
L902 | query_findings
L902 | LIMIT
L902 | OFFSET
L902 | blockers.query_deferred_entities
L902 | EntityKind.sort_col
L902 | sort_vocabulary
L902 | findings.get_stats
L902 | reqs.get_reqs_stats
L903 | query_requirements
L903 | priority="SHOULD"
L903 | types.py
L903 | resolve_severity
L903 | resolve_priority
L903 | resolve_finding_status
L903 | resolve_requirement_status
L903 | _resolve
L903 | ValueError
L903 | is_vocabulary_filter_active
L903 | CB-25
L904 | if severity:
L904 | query_findings(severity=0)
L904 | types.is_vocabulary_filter_active
L904 | unittest.mock.ANY
L904 | __ne__
L904 | tests/test_types.py::TestIsVocabularyFilterActive
L904 | ids
L904 | tags
L904 | id IN ()
L904 | provenance.check_findings
L904 | merge.get_sessions
L904 | types.MERGE_STATUSES
L904 | milestones.list_milestones
L904 | MILESTONE_KINDS
L904 | MILESTONE_STATES
L904 | blockers.query_blockers
L904 | TRIGGER_TYPES
L904 | trigger_type
L904 | CB-29
L904 | CB-28
L905 | CB-82
L905 | bench.import_csv
L905 | date or utc_now()[:10]
L905 | run_id or _next_run_id(conn)
L905 | date=[]
L905 | date={}
L905 | date=""
L905 | sqlite3.ProgrammingError
L905 | json.dumps
L905 | TypeError
L905 | CB-74
L905 | allow_nan=False
L905 | meta={"x": nan}
L905 | x or default
L905 | x is None
L906 | CB-43
L906 | FINDING_STATUSES
L906 | tests/test_dedup.py::TestBranchTotality
L906 | ux_findings_fingerprint_live
L906 | _POST_MIGRATION_INDEXES
L906 | SCHEMA
L906 | _migrate_statuses
L906 | finding_id
L906 | auto:v1:
L906 | db.is_contention
L906 | SQLITE_CONSTRAINT
L906 | CB-61
L906 | findings.normalize_categories
L906 | categories_normalize
L906 | categories-normalize
L906 | apply=True
L906 | --apply
L906 | unverifiable
L906 | CB-51
L906 | findings.import_findings
L906 | CB-9001
L906 | CB-1
L906 | meta.imported_id
L906 | db.txn
L906 | CB-77
L906 | batch_add_findings
L906 | annotate
L906 | CB-97
L906 | escalate=False
L906 | import_findings
L906 | types.severity_rank
L906 | SEVERITIES
L906 | CB-22
L906 | CB-52
L906 | tests/test_dedup.py::TestEscalateOptOutRatchet
L906 | status = 'open'
L906 | CB-41
L906 | CB-37
L906 | CB-53
L906 | stream/security
L906 | CB-35
L906 | total_items: 0
L906 | BT-4
L906 | tags = ?
L906 | promote_tags=False
L906 | tests/test_dedup.py::TestPromoteTagsOptOutRatchet
L906 | add_finding
L906 | json.JSONDecodeError
L906 | PostCommitCorruptionError
L906 | finding_tags
L906 | update_finding(reported_at_ref=)
L906 | query(ref=)
L906 | meta.occurrences[*].meta
L906 | query(meta_key=)
L906 | meta_value
L906 | tests/test_boundary.py::TestBt4FreshnessDeclarations
L906 | tests/test_dedup.py::TestObservationFrozenFields
L906 | BT-5
L906 | attention
L906 | claims._response
L906 | _ATTENTION_SIGNALS_BY_ACTION
L906 | KeyError
L906 | severity_escalated
L906 | bumped
L906 | reopened
L906 | category_divergence
L906 | recurrence_of_closed
L906 | _existing_categories
L906 | AddOutcome
L906 | BumpOutcome
L906 | _bump_row
L906 | _finalize_add
L906 | outputSchema
L906 | additionalProperties: True
L906 | CB-56
L906 | CB-60
L906 | T-59
L906 | CB-160
L906 | occurrences
L906 | occurrences_dropped
L906 | regressed
L906 | recurrence_of
L906 | category_minted
L906 | fingerprint_refusals
L906 | db.resolver_reserved_meta_keys()
L906 | stripped_meta_keys
L906 | CB-15
L906 | resolver_errors
L906 | meta_update
L906 | similar_to
L906 | CB-26
L906 | CB-230
L906 | escalate
L906 | promote_tags
L906 | authored
L906 | CB-247
L906 | authored=False
L906 | updated_at = ?
L906 | loc.py
L906 | tests/test_cb230_service_write.py::TestServiceWriteCallSiteRatchet
L906 | tests/golden/mcp_schema.json
L906 | status=
L908 | CB-60
L908 | types.normalize_category
L908 | finding_id is None
L908 | new_category=True
L908 | --new-category
L908 | meta.category_minted: true
L908 | query(meta_key="category_minted")
L908 | update_finding(meta_update=)
L908 | CB-26
L908 | import_findings
L908 | CB-51
L908 | restore_findings
L908 | findings.normalize_categories
L908 | CB-61
L909 | CB-45
L910 | CB-45
L910 | similarity.py
L910 | findings.similarity_candidates
L910 | meta_json
L910 | CB-24
L910 | ORDER BY created_at, id
L910 | similarity.normalize_text
L910 | findings.normalized_identity_text
L910 | DEFAULT_THRESHOLD = 0.7
L910 | tests/manual/verify_similarity_corpus.py
L910 | MIN_TEXT_LEN = 40
L910 | meta.similar_to
L910 | wont_fix
L910 | not_a_bug
L910 | fixed
L910 | category=""
L910 | categories=("",)
L910 | group_report
L910 | status == "all"
L910 | mock.ANY
L910 | similar_to
L910 | updatable_keys=("similar_to",)
L910 | db.resolver_updatable_meta_keys()
L910 | LIVE_STATUSES
L910 | RECURRENCE_STATUSES
L910 | TestBranchTotality
L910 | similarity_report
L910 | similarity-report
L910 | CB-46
L910 | min_pair_score
L910 | status="all"
L910 | group_report(vectors=)
L911 | entities.EntityRef.set_status
L911 | new_status=
L911 | expected=
L912 | types.is_sql_identifier
L912 | # noqa: S608
L912 | bench.py
L912 | blockers.py
L912 | S608
L912 | pyproject.toml
L912 | [tool.ruff.lint]
L912 | EntityKind.__post_init__
L912 | table
L912 | sort_col
L912 | readable_cols
L912 | dataclasses.replace()
L912 | CB-22
L912 | order_by()
L912 | readable_cols={"(SELECT meta FROM findings)"}
L912 | field()
L912 | fullmatch
L912 | ^…$
L912 | entities._SAFE_IDENT
L912 | types._IDENT
L912 | milestones/capacity.py
L912 | f"{size}_held"
L912 | _held_col()
L912 | OperationalError
L912 | CB-172
L912 | RUF100
