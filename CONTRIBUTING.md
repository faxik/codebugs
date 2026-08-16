# Contributing to codebugs

## Development setup

```bash
git clone https://github.com/faxik/codebugs.git
cd codebugs
uv sync --extra dev
tools/install-hooks.sh          # once per clone — arms the local enforcement
```

`tools/install-hooks.sh` is not optional. Git hooks and git config are per-clone, so they cannot be
merged in — a clone without it silently loses every guard below. It is idempotent; re-run it freely.

## The workflow — `main` is never edited directly

Every code edit happens on a short-lived branch, in a worktree. This is enforced, not suggested:

```bash
tools/worktree-setup.sh fix/cb-42-lease-fencing main
cd .worktrees/fix-cb-42-lease-fencing
# ... work ...
uv run --extra dev python -m pytest tests/ -q
uv run --extra dev ruff check src/ tests/
tools/worktree-finish.sh fix-cb-42-lease-fencing 'fix: fence the merge lease (CB-42)'
```

Branch types are `fix/`, `feature/`, `refactor/`, `docs/` — one concern each; a card-driven branch
carries its id. The pre-commit hook refuses any other shape, and refuses any commit on `main` that
touches something other than a `.claude/plans/*.md` note.

`worktree-finish.sh` runs the guards, forward-merges main into the worktree so conflicts surface
there rather than on main, runs lint and the suite against that combined tree, then integrates with
`git merge --no-ff` under a `flock`. Merged branches are never deleted — only the worktree is
removed.

`CLAUDE.md` carries the full rationale, including why each guard exists and what incident produced
it. The short version: this repo had the workflow as prose for one day, and it was violated within
two hours (CB-50).

## Running tests

**Always from the checkout you changed, and `--extra dev` is not optional in a worktree** — `pytest`
and `ruff` live in `project.optional-dependencies`, which `uv run` does not install by default.

```bash
uv run --extra dev python -m pytest tests/ -q
uv run --extra dev ruff check src/ tests/
```

Never validate a worktree's changes by running the suite from main: `pythonpath = ["src"]` resolves
against the checkout you run in, so that tests main's source and passes on a tree you did not touch.

`ruff check` is the lint gate. `ruff format` is **not** — a large part of the tree predates it. Pin
ruff 0.15.7; 0.16.x flags the whole repo.

## Project structure

```
src/codebugs/
  db.py          — infrastructure: connect(), ids, txn, hook + resolver registries
  types.py       — entity constants and resolvers; zero-dependency
  entities.py    — EntityKind / EntityRef, the one sanctioned cross-table status write
  findings.py    — findings CRUD, identity function, dedup
  reqs.py        — requirements
  server.py      — thin MCP orchestrator (~48 lines), discovers providers via registry
  cli.py         — thin argparse orchestrator (~40 lines), same discovery
  blockers.py  bench.py  merge.py  sweep.py  claims.py  provenance.py
  embeddings.py  similarity.py  fmt.py
  milestones/    — releases, streams, triage, capacity, close gate, reconciliation
tests/           — test_<module>.py, one file per module
tools/           — the worktree harness (see above)
```

## Adding a feature

New domain modules are **self-registering**. Do not edit `server.py` or `cli.py` to add one:

1. Define the schema as a module-level string and provide `ensure_schema(conn)`; call
   `register_schema()` at module level.
2. Define `register_tools(mcp, conn_factory)` and call `register_tool_provider()` at module level.
3. Define `register_cli(sub, commands)` and call `register_cli_provider()` at module level.
4. Add the module import to `_ensure_modules_loaded()` in `db.py` (temporary, until auto-discovery)
   and its mode slug to `SERVER_NAMES` in `server.py` and the `--mode` allowlist in `cli.py`.
5. Add `tests/test_<module>.py`.

A parameter that exists in the domain layer is not reachable until it is declared in the MCP wrapper
*and* the CLI parser *and* the CLI handler. After changing the wire surface, regenerate the golden:

```bash
PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json
```

`PYTHONPATH=src` is load-bearing from a worktree — a bare `python` resolves `codebugs` through the
editable install pointing at the main checkout and would snapshot the wrong tree.

## Design principles

- **AI-first**: the MCP server is the primary interface. Structured JSON, minimal tokens.
- **Modules own their tables.** No module reaches into another's schema; `db.py` never imports a
  domain module at the top level.
- **Per-project**: each project gets its own `.codebugs/findings.db`. No global state.
- **Additive schema changes only**, or an explicit migration function.
- **Parameterized queries exclusively.** An interpolated identifier is validated where it is
  declared, never where it is used.

## Code style

- Python 3.11+, type hints on all public signatures.
- `ruff`, line length 100.
- Keyword-only args after `conn`: `def f(conn, *, name, ...)`.
- MCP tools and CLI handlers carry their domain prefix (`reqs_add`, `cmd_reqs_query`). The findings
  and milestones domains are documented exceptions — see `CLAUDE.md`.
