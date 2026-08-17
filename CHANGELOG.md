# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- **`import_csv` refuses a non-text payload instead of leaking a `TypeError`**
  (CB-75). `csv_data` went straight to `io.StringIO`, so `csv_data=5` raised
  `TypeError: initial_value must be str or None, not int` rather than the
  `ValueError` the module contract promises. The CSV twin of CB-72.

  Scope is the **wrong-type** door only. An ordinary `str` payload can still
  raise `sqlite3.IntegrityError` from inside the insert loop (duplicate row
  labels, duplicate headers, a `nan` metric); that is a different defect, filed
  separately, and an earlier draft of this entry wrongly called this "the last
  door in that family".

  The guard reads `issubclass(type(csv_data), str)` and **not** `isinstance`,
  which is spoofable: CPython honours a `__class__` property, so an object
  declaring `__class__ -> str` — `unittest.mock.MagicMock(spec=str)` is one —
  satisfies `isinstance` and then hits `io.StringIO`'s `TypeError` anyway, i.e.
  the leak surviving its own fix. The rule, which is CB-74's lesson in a second
  form: *the guard's predicate must be identical to the consumer's requirement*.

  Two deliberate asymmetries with `import_json`, both stated because they look
  like inconsistencies otherwise. **Bytes are refused, not decoded**:
  `import_json` widened its annotation to accept them because `json.loads`
  already did, but `io.StringIO` never accepted bytes, so nothing can be
  importing that way today and decoding them would be a new feature rather than
  a fix. **No snapshot is taken**: `import_json` must materialize its list
  because `__iter__` and `__getitem__` can disagree (CB-74), whereas a `str`
  cannot present two views, so there is nothing for a subclass to
  desynchronize — and `isinstance` keeps `str` subclasses working.

  `None` was already raising a `ValueError`, but the wrong one — `io.StringIO(None)`
  yields an empty stream, so the failure surfaced as "CSV must have at least 2
  columns", which describes a malformed header rather than a missing payload. An
  empty string still reaches the parser and raises that message, because an empty
  data payload is supplied content (CB-67).
- **A CLI command that reads a file now reports an unreadable path as one line,
  not a traceback** (CB-71). `bench-import`, `reqs-import` and `import-csv` all
  performed a file read that no exception arm covered, so `codebugs bench-import
  missing.csv -b Q` printed a raw `FileNotFoundError` traceback. `bench-import`
  *had* a `try`, whose only arm was `except (ValueError, json.JSONDecodeError)`
  — the arm existed and did not cover the failure the handler performs;
  `_cmd_reqs_import` had no arm at all and additionally leaked its connection.

  The guard covers **exactly the read**. A handler-wide `except OSError` was
  rejected after measuring it: the success `print` runs after the import has
  committed, and on a closed pipe it raises `BrokenPipeError` — an `OSError` —
  so a wider arm would report a landed import as bad input, the CB-15/CB-16
  success-shaped lie. For the same reason `_cmd_import_csv`'s `open` is hoisted
  out of its `with` statement, since that statement owned the whole import loop
  and a naive wrap would have enclosed both committed rows and the loop's own
  stderr diagnostics.

  While the read guard was going in, the pre-existing arm turned out to be
  laundering a post-commit failure already: it spanned the success `print`, so a
  `ValueError` from a closed stdout came back as one tidy line at exit 1 for a
  run that had committed (measured; the run is visible in `bench-list`
  afterwards). The `print` now sits outside the arm, so such a failure surfaces
  as a crash. Making it POSIX-clean instead — SIGPIPE semantics or the
  `dup2(devnull)` shutdown dance, applied once at the `cli.main` boundary — is a
  product decision tracked as CB-78.

  Four reproduced siblings are filed rather than folded in, because each needs a
  different transformation: the two export handlers truncate their target before
  failing, so the honest fix is write-to-temp-then-rename (CB-76); a read
  failure part-way through CSV import has committed rows behind it and needs a
  reporting contract (CB-77); and `os.getcwd()` in `reqs-verify`/`provenance`
  raises from a deleted cwd, which the file-open sweep structurally could not
  see (CB-79).
- **`import_json` now refuses a malformed payload instead of leaking a stdlib
  exception** (CB-72, CB-74). The function checked that its argument was a
  non-empty list, and nothing else, so two inputs walked past the module's
  contract — *domain functions raise `ValueError` for invalid input* — and out
  to the caller unchanged: a payload outside `str | bytes | bytearray | list`
  as `TypeError` from `json.loads`, and an array whose **elements** are not
  objects as `AttributeError` from `data[0].keys()`.

  The second is the one that mattered: the MCP wire type is `str | list | None`,
  so `codebench_import(benchmark="b", json_data=[1,2])` reached it from a
  client, and the SDK pre-parses a wire string `"[1,2]"` into a list as well.
  The first is in-process only — pydantic refuses a dict before the wrapper
  body runs.

  The guard is a **positive shape check placed before `data[0]`**, never a
  rewrap of `TypeError`/`AttributeError`: a blanket rewrap would also convert a
  post-commit failure inside `import_csv` into a `ValueError`, which
  `_cmd_bench_import`'s arm then reports as bad input for a write that already
  landed — the CB-15/CB-16 lie, re-entering through its own fix. **Every**
  element is checked rather than `data[0]`, because `[{"a":1,"b":2}, 5]` clears
  a first-element check and dies later inside `csv.DictWriter` with the same
  `AttributeError`.

  A supplied list is also **materialized once** and only that snapshot is
  validated and consumed. The check iterates while the code after it *indexes*
  (`data[0]`) and iterates again, so a `list` subclass whose `__iter__`
  disagrees with `__getitem__` could show mappings to the guard and a
  non-mapping to `data[0]` — CB-74's exact `AttributeError`, surviving inside
  its own fix. Validating one view while consuming another is not a guard.

  Two things deliberately still work, each with a test pinning it: `bytes` and
  `bytearray` payloads (accepted by `json.loads`, importing successfully today —
  refusing them would be a behaviour change wearing a bugfix costume, so the
  annotation widened to `str | bytes | bytearray | list` instead), and mappings
  that are not `dict` (`MappingProxyType`, `OrderedDict`), since the guard tests
  `collections.abc.Mapping`.

  One **deliberate narrowing**, stated rather than glossed: a row object that
  merely duck-types `.keys()`/`.get()` without registering as a `Mapping` does
  import on the old code and is refused now. *"An array of objects"* is the
  documented contract, the refusal is loud and at the boundary, and a test
  records the decision so it can be revisited if a real caller appears.
- **`status="deferred"` now honours every other filter instead of discarding it**
  (CB-28). The MCP `query` / `reqs_query` deferred branch forwarded only `limit`
  and `offset`, so `query(status="deferred", severity="critical")` returned **every**
  deferred finding and `reqs_query(status="deferred", priority="must")` every
  deferred requirement. The arguments were known, correctly spelled and correctly
  typed — they passed every validation the package has — and the call returned a
  success payload, so the caller could not tell. `staleness_check(finding_id=…)`
  had the same shape, discarding `status` / `category` / `file` despite its
  docstring promising to forward them.

  `deferred` is a pseudo-status and now resolves to an id restriction, letting the
  owning domain apply its own filters — the shape the April blockers design already
  specified. `blocker_count` annotation and the CB-20 ranked ordering are unchanged,
  and `group_by` works on the deferred path as a result.

  Three sites **refuse** instead, because no path could honour the argument:
  `release_item(status="abandoned", commit=…)` (an abandoned item is reopened and has
  no commit to record), `set_item_status` when the status already matches (no write
  happens — use `mark_integrated`), and `query_findings(meta_value=…)` without
  `meta_key` (the MCP description already declared the key required).

  `blockers.query_deferred_entities` is superseded for this path and kept only for
  its ordering tests.
- **A falsey non-string vocabulary filter no longer silently disables the filter**
  (CB-25). Every vocabulary filter guarded with plain truthiness — `if severity:` —
  which conflates `None` (not supplied), `""` (the documented empty-filter
  convention) and `0` / `False` / `[]` / `{}` (wrong input). CB-19 put the
  non-string refusal inside `types._resolve`, but a falsey value never reached it:
  the guard short-circuited, the condition was never added to the `WHERE` clause,
  and the caller got the **whole table** back. An unfiltered queue is
  indistinguishable from a correctly filtered one, so
  `query_findings(severity=0)` read as a successful, empty-severity query.

  Fixed at `query_findings` (`status`, `severity`), `query_requirements`
  (`status`, `priority`), `reqs_search_similar` (`status`), and
  `staleness_check` / `check_findings`, whose contract differs — `None` and `""`
  mean "default to `open`" there, not "no filter".

  The same sweep closed three filters that validated their vocabulary on the write
  side only: `codemerge_sessions` (`types.MERGE_STATUSES` already existed but was
  dead code, leaving the CHECK constraint as the only enforcement — it is now
  actually used), `milestone_list` (`kind` / `state`, which had `MILESTONE_KINDS` /
  `MILESTONE_STATES` all along and never consulted them on query), and
  `blockers_query` (`trigger_type`, whose validation sat *inside* the truthy guard
  and was therefore skipped wholesale by a falsey value). All three now raise
  `ValueError` on an unknown value instead of returning everything for a falsey one
  and nothing for a misspelled one.

  **Unchanged on purpose:** `None` and `""` still mean "no filter"; list-valued
  filters (`ids`, `tags`) still treat an empty list as "no filter"; and free-text
  filters (`category`, `file`, `source`, `tag`, …) are untouched — they have no
  vocabulary to resolve against, and are tracked as CB-29.

### Changed
- **`severity` now accepts any case, everywhere it is read or written** (CB-19).
  It was the only vocabulary in `types.py` without a resolver, so
  `add_finding(severity="High")` raised while the sibling `resolve_priority("Must")`
  returned `"must"`. Two sibling entities answered the same question differently,
  which is an avoidable failure mode for the LLM callers this tracker serves. (The
  `update` tool docstring did say "Exact lowercase only"; the `add` tool's did not,
  so the strictness was discoverable on one surface and not the other.)
  `types.resolve_severity()` now normalizes case and surrounding whitespace at all
  five sites: `add_finding`, `batch_add_findings`, `update_finding`, the CSV import,
  and `query_findings`. **Severity still has no aliases** — `crit`, `P0` and `sev1`
  are refused. Only spelling is forgiven, never meaning.

- **Vocabulary query filters now resolve instead of comparing raw text**
  (CB-19 and its sibling sweep). Affects `query_findings` (`severity`),
  `query_requirements` (`status`, `priority`) and `reqs_search_similar` (`status`).
  These compared the caller's spelling against a canonical column while the
  corresponding write paths had always normalized, so a value could be written and
  then not found by the same spelling: `update_requirement(priority="SHOULD")`
  stored `should`, and `query_requirements(priority="SHOULD")` returned **zero
  rows**. The failure was silent — "no requirements" is indistinguishable from an
  empty queue.

  **Behaviour change:** a filter value that is not in the vocabulary now raises
  `ValueError` instead of returning an empty result. The `codebugs query` and
  `codebugs reqs-query` CLI handlers report it on stderr and exit 1 rather than
  printing a traceback, which they did not do before for `--status` either. An
  empty-string filter is still treated as "no filter" and is not validated.

### Fixed
- **A named or declared tracker root must now contain a real database** (CB-23).
  `--repo`, `--tracker-root` and `$CODEBUGS_ROOT` accepted any path holding a
  `.codebugs/` *directory*, and `sqlite3.connect` then created a `findings.db`
  inside it — so a mistyped path, or an export inherited by an unrelated process,
  silently became a second, empty tracker whose writes all reported success.
  `_db_path`'s own docstring already promised this branch would "fail loudly
  rather than quietly become a second, empty tracker"; only the check was
  missing. The refusal names the channel that pointed there.

  **The upward walk is deliberately unchanged**: an existing `.codebugs/`
  directory is still the opt-in, and a database is still created inside one that
  has none. That is what makes an interrupted `codebugs init` self-heal — the
  directory is created before the database — and standing inside a directory is
  evidence about where you are in a way a named path is not.

  Structurally, the open-and-migrate half of `connect()` is now `_open()`, and
  `init_project` is its only other caller. Before, `init` created its database
  *by way of* `connect`, so tightening discovery broke the one function allowed
  to create. The named and declared routes open through SQLite's `mode=rw` URI,
  so "must already exist" is enforced by the open itself — the path check alone
  would be a check-then-act, and another agent removing the database in that
  window would get a fresh empty one built for it.

- **`codebugs where` and the MCP preflight now say when the tracker they name
  does not exist yet** (CB-23). `describe_root()` gained an `exists` field,
  reported separately from `error` because resolving is not the same as being
  there: on the walk route a `.codebugs/` with no database resolves cleanly and
  the next write creates the tracker, so nothing errors and nothing was visible.
  That is the CB-13 misbinding's exact shape — the root it mis-binds to is a
  stray directory — and `where` used to print it as the project's tracker.
- **Concurrent `meta` updates no longer erase each other** (CB-24).
  `update_finding` and `update_requirement` merged `notes` / `append_note` /
  `meta_update` in Python over a row they had read in a *separate* statement, then
  wrote the result back — so two writers that both read before either wrote both
  returned success and the later silently discarded the earlier's merge.
  `busy_timeout` serializes the writes and does nothing about the read preceding
  them. This is the harm CB-18 was filed to prevent, reached by another route: the
  tracker exists to coordinate parallel agents, and `append_note` — the one
  operation whose entire purpose is to be additive — is the likeliest to be issued
  concurrently. Both bodies now sit in `db.txn`, so the write lock is held from
  before the read. `milestones.triage_dismiss` gains atomicity as a side effect:
  its dismissal, audit row and status write now commit as one unit, where the
  nested call used to commit the dismissal early.
- **An `EntityKind` carrying a malformed SQL identifier is refused at construction**
  (CB-22). `entities.py` claimed, in a comment, that every interpolated SQL
  identifier (`table` / `sort_col` / readable column) was pattern-checked; only
  `sort_col` was, inside `order_by()`. The `readable_cols` allowlist was the
  material gap — its membership check guards the *caller's* argument and never the
  allowlist's own contents, so a kind declaring `(SELECT meta FROM findings)` as a
  readable column passed the check and `EntityRef.field()` returned a column that is
  not in the allowlist at all. `EntityKind.__post_init__` now validates all three,
  and the pattern lives once as `types.is_sql_identifier()` — applied with
  `fullmatch`, since the previous `^…$` form also matched a trailing newline.
  Exposure was prospective: `ENTITY_KINDS` is a frozen tuple of literals, but the
  test suite already constructs kinds dynamically.
- **`pull_next` no longer loses an agent's capacity increment** (CB-22 sibling).
  `milestones/capacity.py` built the column name `f"{size}_held"` and interpolated
  it, guarded only by a CHECK constraint two layers away on another table. The two
  paths disagreed for identical input: with an existing capacity row an unknown size
  raised `OperationalError`, while with no row it wrote a row of zeros and returned
  **success**, silently dropping the increment. Both now raise `ValueError`.
- **Queues are ordered by declared severity/priority precedence instead of
  alphabetically** (CB-20). `severity` and `priority` are TEXT columns, so a bare
  `ORDER BY` sorted them lexically: findings came back `critical, high, low,
  medium` — ranking `low` above `medium` — and blocked requirements came back
  `could, must, should`, putting the *highest* priority last. Under a `LIMIT`
  this was not cosmetic: asking for the top 3 of a queue holding 3 `medium` and
  3 `low` findings returned three `low` ones and truncated every `medium`.
  Affects `query_findings`, `get_summary`'s severity breakdown, and the deferred
  entity query in `blockers`. The rank is now derived from the `SEVERITIES` /
  `PRIORITIES` tuples via `types.rank_case_sql()`, so the ordering cannot drift
  from the vocabulary, and unrecognised values sort last rather than first.

### Added
- Findings can be **re-triaged**: `severity` is now accepted by
  `update_finding()`, by the `update` MCP tool, and as `codebugs update <id>
  --severity <critical|high|medium|low>` (CB-17). Severity was previously
  write-once, so a card whose impact was re-measured after filing could not be
  corrected in the structured field — the correction had to be carried as prose
  in a note, which is exactly the state a tracker exists to prevent. This brings
  findings level with requirements, whose `priority` was already mutable.
  Validation matches what `add_finding` accepts — see the severity normalization
  entry under Changed, which relaxed both together.
- `codebugs resolve-trailers --range <BASE>..<HEAD> [--repo DIR] [--dry-run]`
  (provenance module): parses `Resolves: CB-N` / `Tightens: CB-N` trailers from
  commit bodies in a git range and flips findings in-process — `Resolves` →
  `fixed` (skipped if already terminal), `Tightens` → appends a progress note.
  Project-agnostic: any repo's `worktree-finish.sh` can call it to auto-close
  findings on integration instead of copying a per-project script. Also exposed
  as `provenance.resolve_trailers(conn, ...)`.
- `--mode` flag for both MCP server and CLI: `findings`, `reqs`, or `all` (default)
  - `codebugs-mcp --mode findings` — exposes only the 7 findings tools
  - `codebugs-mcp --mode reqs` — exposes only the 11 requirements tools
  - `codebugs --mode findings summary` — CLI with filtered subcommands
- `in_progress` finding status for agents claiming tasks
- Status aliases: `done`/`resolved`/`implemented`/`closed` → `fixed`,
  `wontfix` → `wont_fix`, `invalid` → `not_a_bug`,
  `active`/`working`/`in-progress` → `in_progress`
- `resolve_status()` helper in `db` module
- Schema migration for existing databases to support new status

### Changed
- MCP server refactored: tools registered via `register_findings_tools()` / `register_reqs_tools()` instead of module-level decorators. `FastMCP` instance created in `main()` instead of at import time.
- `update_finding()` and `query_findings()` now accept aliases in addition to canonical statuses

### Removed
- `codebugs-findings` and `codereqs-mcp` entry points (use `codebugs-mcp --mode findings|reqs` instead)

## [0.1.0] - 2025-05-01

### Added
- Core finding tracker: add, update, query, stats, summary, categories
- Batch add support for bulk imports
- MCP server (`codebugs-mcp`) with full tool coverage
- CLI (`codebugs`) with add, update, query, stats, summary, categories, import-csv, export-csv
- Requirements tracking module with add, update, query, stats, summary, verify, import/export
- Embedding storage and cosine-similarity search for requirements
- SQLite backend with WAL mode and JSON metadata support
- Test suite (94 tests)
- README, LICENSE (MIT), CONTRIBUTING guide
