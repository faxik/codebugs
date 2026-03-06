# Contributing to codebugs

Thanks for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/faxik/codebugs.git
cd codebugs
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
src/codebugs/
  db.py       — Database layer (all SQLite operations, returns dicts)
  server.py   — MCP server (7 tools via FastMCP)
  cli.py      — CLI (argparse wrapper over db.py)
tests/
  test_db.py  — 41 tests covering all database operations
```

## Design Principles

- **AI-first**: MCP server is the primary interface. Structured JSON responses, minimal tokens.
- **Simple schema**: Lean core fields + flexible `meta` JSON. No migrations needed for new use cases.
- **Zero config**: `pip install` and go. Database created on first use.
- **Per-project**: Each project gets its own `.codebugs/findings.db`. No global state.

## Adding Features

1. Add the database operation to `db.py` (returns dicts, no formatting)
2. Add the MCP tool to `server.py` (thin wrapper)
3. Optionally add CLI command to `cli.py`
4. Add tests to `tests/test_db.py`

## Code Style

- `ruff` for linting (configured in `pyproject.toml`)
- Type hints on all public functions
- No dependencies beyond `mcp` SDK and stdlib
