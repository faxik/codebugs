# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- **`severity` now accepts any case, everywhere it is read or written** (CB-19).
  It was the only vocabulary in `types.py` without a resolver, so
  `add_finding(severity="High")` raised while the sibling `resolve_priority("Must")`
  returned `"must"` — an avoidable failure mode for the LLM callers this tracker
  exists to serve, since nothing in the tool descriptions signalled that one field
  was case-sensitive and its sibling was not. `types.resolve_severity()` now
  normalizes case and surrounding whitespace at all five sites: `add_finding`,
  `batch_add_findings`, `update_finding`, the CSV import, and `query_findings`.
  **Severity still has no aliases** — `crit`, `P0` and `sev1` are refused, and only
  spelling is forgiven, never meaning.

  Note the `query_findings` half is a bug fix in its own right: that filter compared
  the caller's spelling against a canonical column while `status` on the adjacent
  line already resolved, so `query(severity="HIGH")` silently returned **zero rows**
  rather than raising. It now resolves too, which also means an unrecognised filter
  value raises instead of reporting an empty queue.

### Fixed
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
