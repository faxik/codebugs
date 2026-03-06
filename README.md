# codebugs

**Persistent code finding tracker for AI assistants.** SQLite-backed, MCP server + CLI.

AI assistants lose context between sessions. codebugs gives them persistent memory for code review findings, bug reports, and tech debt — with minimal token overhead.

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

| Tool | Purpose |
|------|---------|
| `summary` | Dashboard overview — **start here** for orientation |
| `add` | Log a finding with severity, category, file, description |
| `batch_add` | Log multiple findings at once |
| `update` | Change status, add notes, update tags or metadata |
| `query` | Search/filter with pagination and group-by |
| `stats` | Cross-tabulated counts (severity x category/file/status) |
| `categories` | List existing categories — **call before `add`** for consistency |

### CLI (for humans)

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

Lean core fields with a flexible `meta` JSON column for anything else:

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

## Pattern Detection

The killer feature emerges over time. Categories reveal systemic issues:

```
$ codebugs categories
category                        total  open  fixed
tz_naive_datetime                  15     3     12
n_plus_one                          8     2      6
missing_input_validation            6     4      2
```

If you keep fixing the same category → it's time for a lint rule, pre-commit check, or architectural fix. codebugs turns reactive bug-fixing into proactive prevention.

## Requirements

- Python 3.11+
- No external dependencies beyond the MCP SDK (for the server)
- SQLite (bundled with Python)

## License

MIT
