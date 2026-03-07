# codebugs

**Persistent code finding & requirements tracker for AI assistants.** SQLite-backed, MCP server + CLI.

AI assistants lose context between sessions. codebugs gives them persistent memory for code review findings, bug reports, tech debt, and requirements tracking — with minimal token overhead.

```
Session 1:  Review code → log 50 findings → forget them
Session 2:  summary → instant orientation → fix 20 → update status
Session 3:  summary → "30 open, 20 fixed" → continue
```

No context lost. No re-reading files. No token-heavy recaps.

## Install

```bash
# Global install (recommended)
pipx install codebugs

# Or with pip/uv
pip install codebugs
```

## Setup

### Claude Code (MCP)

Add to `~/.claude.json` (global) or `.mcp.json` (per-project):

```json
{
  "mcpServers": {
    "codebugs": {
      "command": "codebugs-mcp"
    }
  }
}
```

The database lives at `.codebugs/findings.db` in the current working directory — each project gets its own. Add `.codebugs/` to your `.gitignore`.

### Other MCP Clients

Any MCP-compatible client can connect to `codebugs-mcp` via stdio transport.

## Usage

### MCP Tools (for AI assistants)

**Findings** (code review, bugs, tech debt):

| Tool | Purpose |
|------|---------|
| `summary` | Dashboard overview — **start here** for orientation |
| `add` | Log a finding with severity, category, file, description |
| `batch_add` | Log multiple findings at once |
| `update` | Change status, add notes, update tags or metadata |
| `query` | Search/filter with pagination and group-by |
| `stats` | Cross-tabulated counts (severity x category/file/status) |
| `categories` | List existing categories — **call before `add`** for consistency |

**Requirements** (specification tracking):

| Tool | Purpose |
|------|---------|
| `reqs_summary` | Requirements dashboard — **start here** |
| `reqs_add` | Add a requirement (FR-001, priority, status, test coverage) |
| `reqs_update` | Change status, description, priority, test coverage |
| `reqs_query` | Search/filter by status, priority, section, free text |
| `reqs_stats` | Cross-tabulated counts (status x priority) |
| `reqs_verify` | Automated checks: ghost test files, duplicate IDs, status contradictions |
| `reqs_import` | Import from REQUIREMENTS.md (parses markdown tables) |
| `reqs_embed` | Store an embedding vector for a requirement |
| `reqs_batch_embed` | Store embeddings for multiple requirements |
| `reqs_search_similar` | Semantic search across requirements by cosine similarity |
| `reqs_embedding_stats` | Report on embedding coverage |

### CLI (for humans)

**Findings:**

```bash
# Add a finding
codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"

# Dashboard
codebugs summary

# Search
codebugs query --status open --severity critical
codebugs query --group-by file
codebugs query --category n_plus_one

# Update
codebugs update CB-1 --status fixed --notes "Fixed in PR #42"

# Check categories before adding (avoids inconsistent naming)
codebugs categories

# Import/export
codebugs import-csv findings.csv
codebugs export-csv
```

**Requirements:**

```bash
# Import from existing REQUIREMENTS.md
codebugs reqs-import REQUIREMENTS.md

# Dashboard
codebugs reqs-summary

# Verify — find ghost test files, duplicate IDs, status contradictions
codebugs reqs-verify
codebugs reqs-verify --checks tests,status --project-dir /path/to/project

# Search
codebugs reqs-query --status Implemented --priority Must
codebugs reqs-query --search "entity" --group-by section

# Update
codebugs reqs-update FR-090 --status Superseded --notes "Replaced by vault architecture"

# Add
codebugs reqs-add FR-700 -d "System shall support licensing" --section "1.72 Licensing" --priority Must

# Export back to markdown
codebugs reqs-export REQUIREMENTS.md
```

## How It Works

### The Problem

AI code review sessions produce findings that get lost:
- Findings live in chat context → gone when the session ends
- Re-reading files wastes tokens on re-discovery
- No way to track progress across sessions

### The Solution

codebugs stores findings in a local SQLite database. AI assistants write findings as they discover them, then query the database in future sessions for instant context recovery.

**Token savings**: A `summary` call returns a structured JSON overview in ~200 tokens. Without codebugs, re-establishing the same context costs 2,000-10,000+ tokens of file reading and conversation history.

### Typical Workflow

1. **Review**: AI reviews code, calls `categories` to check naming, then `add` for each finding
2. **Fix**: Next session, AI calls `summary` → sees 50 open findings → `query --severity critical` → fixes the worst ones → `update` each as `fixed`
3. **Track**: Over time, `categories` reveals patterns — "12 `tz_naive_datetime` fixed across 9 files → time for a lint rule"

## Schema

Both tables share the same SQLite database (`.codebugs/findings.db`) with flexible JSON columns.

### Findings

| Field | Type | Description |
|-------|------|-------------|
| `id` | text | Auto-generated (`CB-1`, `CB-2`, ...) or user-provided |
| `severity` | text | `critical`, `high`, `medium`, `low` |
| `category` | text | User-defined (e.g. `n_plus_one`, `missing_validation`) |
| `file` | text | File path relative to project root |
| `status` | text | `open`, `fixed`, `not_a_bug`, `wont_fix`, `stale` |
| `description` | text | What's wrong |
| `source` | text | Who created it (`claude`, `ruff`, `human`, `mypy`, ...) |
| `tags` | json | Array of strings for ad-hoc grouping |
| `meta` | json | Anything else: `lines`, `module`, `rule_code`, `cwe_id`, ... |

### Requirements

| Field | Type | Description |
|-------|------|-------------|
| `id` | text | User-provided (`FR-001`, `FR-002`, ...) |
| `section` | text | Grouping (e.g. `1.10 Document Sorting`) |
| `description` | text | What the system shall do |
| `priority` | text | `Must`, `Should`, `Could` |
| `status` | text | `Planned`, `Partial`, `Implemented`, `Verified`, `Superseded`, `Obsolete` |
| `source` | text | Origin (e.g. `Take 26`, `NEW`, `R&A`) |
| `test_coverage` | text | Test file name(s) |
| `embedding` | blob | Optional float32 vector for semantic search |
| `tags` | json | Array of strings |
| `meta` | json | Anything else: `notes`, `superseded_by`, ... |

## Pattern Detection

The killer feature for findings emerges over time. Categories reveal systemic issues:

```
$ codebugs categories
category                        total  open  fixed
tz_naive_datetime                  15     3     12
n_plus_one                          8     2      6
missing_input_validation            6     4      2
```

If you keep fixing the same category → it's time for a lint rule, pre-commit check, or architectural fix. codebugs turns reactive bug-fixing into proactive prevention.

## Requirements Verification

The killer feature for requirements is `reqs-verify` — automated detection of documentation rot:

```
$ codebugs reqs-verify
Verified 683 requirements.

12 issue(s) found:

check   sev       id      message
------  --------  ------  --------------------------------------------------
tests   high      FR-350  Test file not found: test_entity_graph.py
tests   high      FR-351  Test file not found: test_entity_graph.py
status  high      FR-090  Description mentions 'superseded' but status is 'Planned'
status  medium    FR-006  Must-priority requirement implemented without test coverage
ids     medium    --      Numbering gaps (5+): FR-025..FR-029, FR-316..FR-329
```

Run it after any documentation change to catch contradictions before they become misleading.

## Semantic Requirements Search

Store embeddings for requirements to enable semantic search — find related requirements even when the wording is different:

```bash
# Via MCP: store embeddings (caller generates vectors via embedding API)
reqs_embed(req_id="FR-001", embedding=[0.1, 0.2, ...])
reqs_batch_embed(embeddings={"FR-001": [...], "FR-002": [...]})

# Search for similar requirements
reqs_search_similar(query_embedding=[0.1, 0.2, ...], limit=5, min_similarity=0.3)
```

Embeddings are stored as float32 BLOBs in the same SQLite database. Search uses brute-force cosine similarity — fast enough for thousands of requirements.

## Requirements

- Python 3.11+
- No external dependencies beyond the MCP SDK (for the server)
- SQLite (bundled with Python)

## License

MIT
