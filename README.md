# codebugs

AI-native code finding tracker. SQLite-backed, MCP server + CLI.

Designed for AI assistants (Claude, etc.) to efficiently manage code review findings, bug reports, and tech debt — with minimal token overhead.

## Why?

AI assistants lose context between sessions. codebugs gives them persistent memory for code findings:

- **Session 1**: Review code, log 50 findings → write to DB, forget
- **Session 2**: `summary` → instant orientation, no re-reading
- **Session 3**: `query --status open` → continue where you left off

Token savings: structured JSON instead of holding findings in conversation context.

## Install

```bash
pip install codebugs
# or
uv pip install codebugs
```

## Quick Start

### As MCP Server (primary interface)

Add to your Claude Code MCP config (`.mcp.json`):

```json
{
  "mcpServers": {
    "codebugs": {
      "command": "python",
      "args": ["-m", "codebugs.server"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

The database is created at `.codebugs/findings.db` in the project directory. Add `.codebugs/` to your `.gitignore`.

### As CLI

```bash
# Add a finding
codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"

# Check status
codebugs summary

# Search
codebugs query --status open --severity critical
codebugs query --group-by file

# Update
codebugs update CB-1 --status fixed

# List categories (for consistency)
codebugs categories

# Import/export
codebugs import-csv findings.csv
codebugs export-csv
```

## MCP Tools

| Tool | Purpose |
|------|---------|
| `add` | Create a finding with severity, category, file, description |
| `batch_add` | Create multiple findings at once |
| `update` | Change status, notes, tags, or metadata |
| `query` | Search/filter with pagination and group-by |
| `stats` | Cross-tabulated severity × category/file/status |
| `summary` | Dashboard overview — start here |
| `categories` | List existing categories (call before `add` for consistency) |

## Schema

Findings have a lean core with a flexible `meta` JSON field:

| Field | Type | Description |
|-------|------|-------------|
| `id` | text | Auto-generated (CB-1, CB-2, ...) or user-provided |
| `severity` | text | critical, high, medium, low |
| `category` | text | User-defined (e.g. `n_plus_one`, `missing_validation`) |
| `file` | text | File path relative to project root |
| `status` | text | open, fixed, not_a_bug, wont_fix, stale |
| `description` | text | What's wrong |
| `source` | text | Who created it (claude, ruff, human, mypy, ...) |
| `tags` | json | Array of strings for grouping |
| `meta` | json | Anything else (lines, module, rule_code, cwe_id, ...) |

## Workflow: Pattern Detection

Over time, categories reveal systemic issues:

```
$ codebugs categories
category                        total  open  fixed
tz_naive_datetime                  15     3     12
n_plus_one                          8     2      6
missing_input_validation            6     4      2
```

If you keep fixing the same category → it's time for a lint rule or pre-commit check.

## License

MIT
