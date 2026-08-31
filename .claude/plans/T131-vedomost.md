# T-131 — ведомость переезда

**Состояние: ПЕРЕЕЗД ВЫПОЛНЕН.** Каждая строка — единица исходного файла и то, куда
физически уехали её байты. Ни одно предложение не переписано: файлы собраны
склейкой исходных кусков, и это проверено оракулом, а не обещано.

## Чем закрыт каждый пункт §5

| Пункт §5 | Чем закрыт | Результат |
|---|---|---|
| 1. полный набор зелен числом | `pytest tests/ -q` на неподвижном дереве | см. §15 брифа |
| 2. четыре пинующих утверждения | прогон по именам классов | 13 passed |
| 3. множества сохранены по ОБЪЕДИНЕНИЮ | `T126-idents.py` над корнем + вложенными + справками | 0 потерянных карт, 0 имён тестов, 0 путей, 1 псевдотокен |
| 4. потолок размера зелен | `tests/test_claude_md_size_ceiling.py` | 7 passed |
| сохранение текста | оракул сборки: каждый чанк найден в своём файле | 883/883 |
| проверка границ | `boundary_check.py` по смежности | 3 разрыва найдено и починено |

## Итог по адресам

| адрес | байт | доля |
|---|---:|---:|
| `src/codebugs/CLAUDE.md` | 110266 | 56.6 % |
| справка `docs/claude-md-rationale/` | 41453 | 21.3 % |
| корень `CLAUDE.md` | 38081 | 19.5 % |
| `tests/CLAUDE.md` | 5165 | 2.6 % |
| **всего** | **194965** | 100 % |

## Правки связности, каждая с причиной

Единственные места, где адрес выбран не поабзацным разбором, а мною — и потому
названы поимённо:

- **единица 5:4** → корень `CLAUDE.md`: restores the antecedent for 'The narrowed row is still a checkable claim', which was left in the root with nothing to refer to
- **единица 7:0** → справка `docs/claude-md-rationale/`: numbered item 2 whose siblings 1,3,4 are rationale and left; an orphaned '2.' reads as a document defect, and its operational essence ('the block says in words not to re-run') is already stated in the root paragraph above
- **единица 7:1** → справка `docs/claude-md-rationale/`: same numbered item, second chunk
- **единица 14:35** → корень `CLAUDE.md`: honest-scope clause qualifying the byte-boundary rule that stayed in the root; a rule whose 'no test discriminates this' caveat left reads as a wider promise than it keeps
- **единица 75:9** → `src/codebugs/CLAUDE.md`: the 'this count is a question for the ratchet, not for this paragraph' boundary belongs with the ratchet rule, which stayed in the nested file
- **единица 132:2** → `src/codebugs/CLAUDE.md`: opens with 'SIG_DFL fixes both' whose two antecedent states left the root; the 141 vocabulary an operator needs is fully carried by the Claims exit-code list
- **единица 134:4** → `src/codebugs/CLAUDE.md`: not history at all — the BOTH-halves requirement of the /dev/stdout alias check, an operative part of the rule above it

## Построчно

| № | раздел | подраздел | байт | куда уехало | начало |
|---:|---|---|---:|---|---|
| 0 | (preamble) | — | 10 | корень `CLAUDE.md` 10 | # Codebugs |
| 1 | (preamble) | — | 93 | корень `CLAUDE.md` 93 |  AI-native code finding & requirements tracker. SQLite-backed, exposed |
| 2 | Workflow — `main` is nev | — | 47 | корень `CLAUDE.md` 47 | ## Workflow — `main` is never edited directly |
| 3 | Workflow — `main` is nev | — | 1015 | справка `docs/claude-md-rationale/` 714 + корень `CLAUDE.md` 301 |  **Every code edit happens on a short-lived branch, in a worktree, and |
| 4 | Workflow — `main` is nev | — | 1647 | корень `CLAUDE.md` 1647 | | Rule | Mechanism | Refuses with | |---|---|---| | Branch carries `fi |
| 5 | Workflow — `main` is nev | — | 2763 | корень `CLAUDE.md` 1942 + справка `docs/claude-md-rationale/` 821 | **`.github/workflows/main-invariants.yml` is deliberately NOT in that  |
| 6 | Workflow — `main` is nev | — | 914 | справка `docs/claude-md-rationale/` 914 | 1. **Identity, not shape.** A two-parent tip does not establish that m |
| 7 | Workflow — `main` is nev | — | 315 | справка `docs/claude-md-rationale/` 315 | 2. **`tip-not-ours` is usually benign, and the text says so.** A plan  |
| 8 | Workflow — `main` is nev | — | 845 | справка `docs/claude-md-rationale/` 845 | 3. **The block is delivered from an `EXIT` trap armed the instant the  |
| 9 | Workflow — `main` is nev | — | 2990 | справка `docs/claude-md-rationale/` 2431 + корень `CLAUDE.md` 559 | 4. **The reads are `--no-replace-objects` and stdout-only.** Replace r |
| 10 | Workflow — `main` is nev | — | 170 | справка `docs/claude-md-rationale/` 170 | - Candidates are **every ref pointing at that head**, always. There is |
| 11 | Workflow — `main` is nev | — | 354 | справка `docs/claude-md-rationale/` 354 | - **Every local branch must qualify.** With "any qualifies" there is a |
| 12 | Workflow — `main` is nev | — | 357 | справка `docs/claude-md-rationale/` 357 | - A **remote ref other than main's upstream `main` neither qualifies n |
| 13 | Workflow — `main` is nev | — | 146 | справка `docs/claude-md-rationale/` 146 | - Upstream **`main` wins** over a non-qualifying local branch, so a st |
| 14 | Workflow — `main` is nev | — | 16848 | справка `docs/claude-md-rationale/` 10324 + корень `CLAUDE.md` 6524 | - The trusted ref is matched **exactly** (`refs/remotes/<branch.main.r |
| 15 | Workflow — `main` is nev | — | 248 | справка `docs/claude-md-rationale/` 162 + корень `CLAUDE.md` 86 | 1. It is scoped to a **pinned baseline SHA**, since main's history pre |
| 16 | Workflow — `main` is nev | — | 602 | корень `CLAUDE.md` 370 + справка `docs/claude-md-rationale/` 232 | 2. **Anything merge-shaped is invisible to it**, and `amend`/`rebase`/ |
| 17 | Workflow — `main` is nev | — | 335 | корень `CLAUDE.md` 242 + справка `docs/claude-md-rationale/` 93 | 3. It uses **`--no-renames`**, and that is not cosmetic: with rename d |
| 18 | Workflow — `main` is nev | — | 1932 | корень `CLAUDE.md` 1397 + справка `docs/claude-md-rationale/` 535 | 4. **A workflow cannot refuse a push by itself** — it reports afterwar |
| 19 | Workflow — `main` is nev | — | 685 | корень `CLAUDE.md` 456 + справка `docs/claude-md-rationale/` 229 | 5. **`main-invariants.yml` deliberately does not subscribe to `pull_re |
| 20 | Workflow — `main` is nev | — | 9331 | корень `CLAUDE.md` 5573 + справка `docs/claude-md-rationale/` 3758 | 6. It needs **`fetch-depth: 0`** because the AUDIT step reads history: |
| 21 | Workflow — `main` is nev | — | 2796 | корень `CLAUDE.md` 2058 + справка `docs/claude-md-rationale/` 738 | - **Create:** `tools/worktree-setup.sh <type>/<slug> [base]`, which va |
| 22 | Workflow — `main` is nev | — | 274 | корень `CLAUDE.md` 274 | - **Worktrees live in `.worktrees/`,** slug = branch with `/`→`-`, mat |
| 23 | Workflow — `main` is nev | — | 303 | корень `CLAUDE.md` 303 | - **Then work there, entirely.** Check which checkout you are in befor |
| 24 | Workflow — `main` is nev | — | 1054 | корень `CLAUDE.md` 1054 | - **Tests and lint run in the worktree, and it needs its own environme |
| 25 | Workflow — `main` is nev | — | 619 | корень `CLAUDE.md` 619 | - **Integrate with `tools/worktree-finish.sh <slug> ['commit msg'] [-- |
| 26 | Workflow — `main` is nev | — | 2434 | корень `CLAUDE.md` 1385 + справка `docs/claude-md-rationale/` 1049 | - **The integration message follows `Merge <branch>: <what changed> (C |
| 27 | Workflow — `main` is nev | — | 905 | корень `CLAUDE.md` 598 + справка `docs/claude-md-rationale/` 307 | - **Every re-run hint echoes back the `--merge-msg` the aborted run wa |
| 28 | Workflow — `main` is nev | — | 231 | корень `CLAUDE.md` 231 | - **`ruff check` is the lint gate; `ruff format` is deliberately not** |
| 29 | Workflow — `main` is nev | — | 189 | корень `CLAUDE.md` 189 | - **Session end:** `git status` clean in main *and* in every worktree, |
| 30 | Workflow — `main` is nev | — | 6366 | справка `docs/claude-md-rationale/` 4746 + корень `CLAUDE.md` 1620 | - **The only thing that may land on main directly** is a `.claude/plan |
| 31 | Releasing | — | 13 | корень `CLAUDE.md` 13 | ## Releasing  |
| 32 | Releasing | — | 185 | корень `CLAUDE.md` 185 | 1. Bump `version` in `pyproject.toml` and `__version__` in `src/codebu |
| 33 | Releasing | — | 189 | корень `CLAUDE.md` 189 | 2. Retitle `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] — <date> |
| 34 | Releasing | — | 182 | корень `CLAUDE.md` 182 | 3. **After** the branch lands, tag the merge commit from the primary c |
| 35 | Architecture | — | 16 | корень `CLAUDE.md` 16 | ## Architecture  |
| 36 | Architecture | — | 268 | корень `CLAUDE.md` 268 | - **Domain modules** (`src/codebugs/`): `db.py` (findings + shared inf |
| 37 | Architecture | — | 171 | корень `CLAUDE.md` 171 | - **Shared types** (`types.py`): Entity constants (statuses, prioritie |
| 38 | Architecture | — | 250 | корень `CLAUDE.md` 250 | - **MCP server** (`server.py`): Thin `MCPServer` orchestrator (~48 lin |
| 39 | Architecture | — | 576 | корень `CLAUDE.md` 444 + справка `docs/claude-md-rationale/` 132 | - **CLI** (`cli.py`): Thin argparse orchestrator. Discovers CLI provid |
| 40 | Architecture | — | 171 | корень `CLAUDE.md` 171 | - **Formatting** (`fmt.py`): Shared CLI output utilities (ASCII table  |
| 41 | Architecture | — | 258 | корень `CLAUDE.md` 258 | - **Filesystem output** (`fsio.py`): `atomic_write` — the only sanctio |
| 42 | Architecture | — | 121 | корень `CLAUDE.md` 121 | - **Storage**: Single SQLite DB at `.codebugs/findings.db`; each domai |
| 43 | Architecture | Known architectu | 29 | `src/codebugs/CLAUDE.md` 29 | ### Known architectural debt  |
| 44 | Architecture | Known architectu | 502 | `src/codebugs/CLAUDE.md` 502 | - ~~Staleness/provenance logic pending extraction~~ — **done, with one |
| 45 | Architecture | Known architectu | 322 | `src/codebugs/CLAUDE.md` 322 | - **`db.connect()` import trigger**: `_ensure_modules_loaded()` still  |
| 46 | Architecture | Known architectu | 273 | справка `docs/claude-md-rationale/` 273 | - ~~`blockers.py` cross-module reach into private `_row_to_dict`~~ — * |
| 47 | Architecture | Known architectu | 469 | `src/codebugs/CLAUDE.md` 469 | - **Findings naming exception**: The findings domain predates the nami |
| 48 | Architecture | Known architectu | 352 | `src/codebugs/CLAUDE.md` 352 | - **Milestones naming exception**: The milestones spec mandates spec-c |
| 49 | Architecture | Known architectu | 475 | `src/codebugs/CLAUDE.md` 475 | - **Post-add hook**: `db.register_post_add_hook(name, fn)` is the exte |
| 50 | Architecture | Known architectu | 2469 | `src/codebugs/CLAUDE.md` 2469 | - **Pre-add resolver seam: built with its first consumer (CB-45; CB-44 |
| 51 | Architecture | Known architectu | 631 | `src/codebugs/CLAUDE.md` 631 | - **Status-change hook**: `db.register_status_change_hook(name, fn)` i |
| 52 | Architecture | Known architectu | 2513 | `src/codebugs/CLAUDE.md` 2513 | - **A DERIVED queue must not be trusted to a write-time hook alone (CB |
| 53 | Architecture | Known architectu | 2579 | `src/codebugs/CLAUDE.md` 2579 | - **The read-side filter is a SEAM, and its justification is the WRITE |
| 54 | Architecture | Known architectu | 604 | справка `docs/claude-md-rationale/` 467 + `src/codebugs/CLAUDE.md` 137 | - **A VIEW was rejected for a measured reason, not the obvious one.**  |
| 55 | Code rules | — | 14 | корень `CLAUDE.md` 14 | ## Code rules  |
| 56 | Code rules | Module structure | 20 | `src/codebugs/CLAUDE.md` 20 | ### Module structure |
| 57 | Code rules | Module structure | 132 | `src/codebugs/CLAUDE.md` 132 | - Each domain module owns its schema, constants, and public functions. |
| 58 | Code rules | Module structure | 142 | `src/codebugs/CLAUDE.md` 142 | - `db.py` is infrastructure — it provides `connect()`, ID generation,  |
| 59 | Code rules | Module structure | 142 | `src/codebugs/CLAUDE.md` 142 | - Domain modules may import `db` for connection/ID utilities. They mus |
| 60 | Code rules | Naming and style | 20 | корень `CLAUDE.md` 20 | ### Naming and style |
| 61 | Code rules | Naming and style | 61 | корень `CLAUDE.md` 61 | - Python 3.11+. Type hints on all public function signatures. |
| 62 | Code rules | Naming and style | 49 | корень `CLAUDE.md` 49 | - `ruff` for linting/formatting, line length 100. |
| 63 | Code rules | Naming and style | 83 | `src/codebugs/CLAUDE.md` 83 | - Public functions use keyword-only args after `conn`: `def f(conn, *, |
| 64 | Code rules | Naming and style | 166 | `src/codebugs/CLAUDE.md` 166 | - MCP tool functions are prefixed with the domain: `codebench_import`, |
| 65 | Code rules | Naming and style | 128 | `src/codebugs/CLAUDE.md` 128 | - CLI handlers are named `cmd_<domain>_<action>()`. (Exception: findin |
| 66 | Code rules | Database | 12 | `src/codebugs/CLAUDE.md` 12 | ### Database |
| 67 | Code rules | Database | 464 | `src/codebugs/CLAUDE.md` 464 | - **DB discovery**: `db.connect()` walks up from cwd for an existing ` |
| 68 | Code rules | Database | 1471 | `src/codebugs/CLAUDE.md` 933 + справка `docs/claude-md-rationale/` 538 | - **What counts as "a tracker" differs by how you got there, and the a |
| 69 | Code rules | Database | 1252 | `src/codebugs/CLAUDE.md` 1252 | - **Discovery is a heuristic, so it has a declared override — and ever |
| 70 | Code rules | Database | 765 | `src/codebugs/CLAUDE.md` 638 + справка `docs/claude-md-rationale/` 127 | - **Some layouts are provably undiscoverable, and the honest response  |
| 71 | Code rules | Database | 799 | `src/codebugs/CLAUDE.md` 799 | - **A binding you cannot see is a binding you cannot debug.** `db.desc |
| 72 | Code rules | Database | 6209 | `src/codebugs/CLAUDE.md` 5998 + справка `docs/claude-md-rationale/` 211 | - **`exists` IS THREE-VALUED, and the third value is the DEFAULT rathe |
| 73 | Code rules | Database | 3875 | `src/codebugs/CLAUDE.md` 3875 | - **THE UPWARD WALK IS THREE-VALUED TOO, AND WHAT IT DOES WITH THE THI |
| 74 | Code rules | Database | 1763 | `src/codebugs/CLAUDE.md` 1763 | - **An ENVIRONMENTAL sqlite failure is classified inside `_open` and r |
| 75 | Code rules | Database | 3143 | `src/codebugs/CLAUDE.md` 3143 | - **One classification point covers OPENING A CONNECTION — never a wri |
| 76 | Code rules | Database | 125 | `src/codebugs/CLAUDE.md` 125 | - Each module defines its schema as a module-level string (`SCHEMA` or |
| 77 | Code rules | Database | 114 | `src/codebugs/CLAUDE.md` 114 | - All schema changes must be additive (new tables, new columns with de |
| 78 | Code rules | Database | 75 | корень `CLAUDE.md` 75 | - Use parameterized queries exclusively. Never interpolate values into |
| 79 | Code rules | Database | 296 | `src/codebugs/CLAUDE.md` 296 | - SQLite WAL mode is enabled. `db.connect()` also sets `busy_timeout=5 |
| 80 | Code rules | Database | 692 | `src/codebugs/CLAUDE.md` 692 | - **Never write a plain `BEGIN`.** It pins a read snapshot, and the la |
| 81 | Code rules | Database | 991 | `src/codebugs/CLAUDE.md` 774 + справка `docs/claude-md-rationale/` 217 | - **Assigning `conn.isolation_level` COMMITS an open transaction — so  |
| 82 | Code rules | Database | 811 | `src/codebugs/CLAUDE.md` 535 + справка `docs/claude-md-rationale/` 276 | - **A refusal path that writes nothing needs no rollback machinery — a |
| 83 | Code rules | Database | 1583 | `src/codebugs/CLAUDE.md` 1122 + справка `docs/claude-md-rationale/` 461 | - **A deadline computed in Python is a defect waiting for something sl |
| 84 | Code rules | Database | 968 | `src/codebugs/CLAUDE.md` 653 + справка `docs/claude-md-rationale/` 315 | - **An idempotency affordance can defeat the gate it sits in front of  |
| 85 | Code rules | Database | 1516 | `src/codebugs/CLAUDE.md` 1516 | - **A value computed in Python from a row you just read must be writte |
| 86 | Code rules | Database | 2164 | `src/codebugs/CLAUDE.md` 1901 + справка `docs/claude-md-rationale/` 263 | - **The CB-24 population is ~19 sites, not the four that got fixed — a |
| 87 | Code rules | Database | 379 | `src/codebugs/CLAUDE.md` 379 | - **The `RETURNING` rule.** A statement either carries `RETURNING` and |
| 88 | Code rules | Database | 1270 | `src/codebugs/CLAUDE.md` 1270 | - **One assignment per column in a built `SET` clause.** Update functi |
| 89 | Code rules | Database | 2387 | `src/codebugs/CLAUDE.md` 2387 | - **A column settable at INSERT should be settable at UPDATE, or docum |
| 90 | Code rules | Database | 732 | `src/codebugs/CLAUDE.md` 505 + справка `docs/claude-md-rationale/` 227 | - **Match a field's own insert contract, not its neighbour's — and whe |
| 91 | Code rules | Database | 1235 | `src/codebugs/CLAUDE.md` 1235 | - **Never `ORDER BY` a vocabulary column directly — it sorts alphabeti |
| 92 | Code rules | Database | 1443 | `src/codebugs/CLAUDE.md` 1443 | - **A vocabulary must resolve on BOTH sides of the entity — the write  |
| 93 | Code rules | Database | 2156 | `src/codebugs/CLAUDE.md` 2156 | - **"No filter" is `None` and `""` — never truthiness, and never decid |
| 94 | Code rules | Database | 2125 | `src/codebugs/CLAUDE.md` 1811 + справка `docs/claude-md-rationale/` 314 | - **On a WRITE path, `None` is the only "not supplied" — and that is d |
| 95 | Code rules | Database | 16051 | `src/codebugs/CLAUDE.md` 16051 | - **Findings have an identity function (CB-43): `add` is an upsert, no |
| 96 | Code rules | Database | 2315 | `src/codebugs/CLAUDE.md` 2315 | - **Category spelling is normalized and MINTING a new category is gate |
| 97 | Code rules | Database | 517 | `src/codebugs/CLAUDE.md` 517 | - **Requirements deliberately have NO identity function** — DECIDED on |
| 98 | Code rules | Database | 3247 | `src/codebugs/CLAUDE.md` 3247 | - **Similarity extension (CB-45): `similarity.py` is the package's FIR |
| 99 | Code rules | Database | 348 | `src/codebugs/CLAUDE.md` 348 | - **The one sanctioned cross-table status write** is `entities.EntityR |
| 100 | Code rules | Database | 7487 | `src/codebugs/CLAUDE.md` 6001 + справка `docs/claude-md-rationale/` 1486 | - **An interpolated SQL identifier is validated BEFORE it reaches the  |
| 101 | Code rules | Error handling | 18 | `src/codebugs/CLAUDE.md` 18 | ### Error handling |
| 102 | Code rules | Error handling | 92 | `src/codebugs/CLAUDE.md` 92 | - Domain functions raise `ValueError` for invalid input and `KeyError` |
| 103 | Code rules | Error handling | 81 | `src/codebugs/CLAUDE.md` 81 | - MCP tools let exceptions propagate to the MCP server's built-in erro |
| 104 | Code rules | Error handling | 78 | `src/codebugs/CLAUDE.md` 78 | - CLI handlers catch domain exceptions and print to stderr with `sys.e |
| 105 | Code rules | Error handling | 1877 | `src/codebugs/CLAUDE.md` 1616 + справка `docs/claude-md-rationale/` 261 | - **A failure raised AFTER the commit must never be reported through t |
| 106 | Code rules | Error handling | 1647 | `src/codebugs/CLAUDE.md` 1647 | - **`OSError` arrives from ambient sources, not just from `open()` — a |
| 107 | Code rules | Error handling | 1956 | `src/codebugs/CLAUDE.md` 1446 + справка `docs/claude-md-rationale/` 510 | - **A per-row swallow inside an import loop catches the row-level exce |
| 108 | Code rules | Error handling | 153 | `src/codebugs/CLAUDE.md` 153 | - All MCP tools return `dict[str, Any]`. → почему именно так: `docs/cl |
| 109 | Code rules | Testing | 11 | корень `CLAUDE.md` 11 | ### Testing |
| 110 | Code rules | Testing | 106 | `tests/CLAUDE.md` 106 | - Tests live in `tests/test_<module>.py`. Most test classes use a fres |
| 111 | Code rules | Testing | 104 | `tests/CLAUDE.md` 104 | - Tests requiring `db.connect()`, cross-module schemas, or git operati |
| 112 | Code rules | Testing | 5189 | `tests/CLAUDE.md` 3287 + справка `docs/claude-md-rationale/` 1902 | - Each test file defines its own fixtures. `tests/conftest.py` is not  |
| 113 | Code rules | Testing | 60 | `tests/CLAUDE.md` 60 | - Test the domain module's public API, not internal helpers. |
| 114 | Code rules | Testing | 1504 | `tests/CLAUDE.md` 1504 | - **A concurrency test's ASSERTION is the hard part, not its schedulin |
| 115 | Code rules | Testing | 48 | корень `CLAUDE.md` 48 | - Run tests: `uv run python -m pytest tests/ -v` |
| 116 | Code rules | Testing | 43 | корень `CLAUDE.md` 43 | - Run lint: `uv run ruff check src/ tests/` |
| 117 | Code rules | Testing | 152 | `tests/CLAUDE.md` 104 + корень `CLAUDE.md` 48 | - Run format: `uv run ruff format src/ tests/` → почему именно так: `d |
| 118 | Code rules | MCP tool registr | 25 | `src/codebugs/CLAUDE.md` 25 | ### MCP tool registration |
| 119 | Code rules | MCP tool registr | 118 | `src/codebugs/CLAUDE.md` 118 | - Each domain module defines `register_tools(mcp, conn_factory)` and c |
| 120 | Code rules | MCP tool registr | 92 | `src/codebugs/CLAUDE.md` 92 | - `server.py` discovers providers via the registry and passes `_conn`  |
| 121 | Code rules | MCP tool registr | 120 | `src/codebugs/CLAUDE.md` 120 | - Tool parameters that accept JSON should use `str | list | None` (not |
| 122 | Code rules | MCP tool registr | 129 | `src/codebugs/CLAUDE.md` 129 | - New modules: define `register_tools(mcp, conn_factory)`, call `regis |
| 123 | Code rules | MCP tool registr | 1556 | `src/codebugs/CLAUDE.md` 1556 | - **A declared argument must reach its query, or the call must fail —  |
| 124 | Code rules | MCP tool registr | 840 | справка `docs/claude-md-rationale/` 438 + `src/codebugs/CLAUDE.md` 402 | - **Unknown argument names are refused, not ignored.** `server.install |
| 125 | Code rules | MCP tool registr | 1362 | `src/codebugs/CLAUDE.md` 1012 + справка `docs/claude-md-rationale/` 350 | - **What a client SEES must not depend on which interpreter built the  |
| 126 | Code rules | MCP tool registr | 651 | `src/codebugs/CLAUDE.md` 651 | - **A parameter that exists in the domain layer is not reachable until |
| 127 | Code rules | CLI | 7 | `src/codebugs/CLAUDE.md` 7 | ### CLI |
| 128 | Code rules | CLI | 111 | `src/codebugs/CLAUDE.md` 111 | - Each domain module defines `register_cli(sub, commands)` and calls ` |
| 129 | Code rules | CLI | 77 | `src/codebugs/CLAUDE.md` 77 | - `cli.py` discovers providers via the registry and filters by `--mode |
| 130 | Code rules | CLI | 120 | `src/codebugs/CLAUDE.md` 120 | - New modules: define `register_cli(sub, commands)`, call `register_cl |
| 131 | Code rules | CLI | 527 | `src/codebugs/CLAUDE.md` 527 | - **Two commands are built into `cli.py` rather than owned by a domain |
| 132 | Code rules | CLI | 1631 | `src/codebugs/CLAUDE.md` 1329 + справка `docs/claude-md-rationale/` 302 | - **The process entry point is `cli.run`, not `cli.main`, and the spli |
| 133 | Code rules | CLI | 3944 | корень `CLAUDE.md` 1776 + `src/codebugs/CLAUDE.md` 1642 + справка `docs/claude-md-rationale/` 526 | - **A CLOSED stdout is a different state from a dead reader (CB-134).* |
| 134 | Code rules | CLI | 3171 | `src/codebugs/CLAUDE.md` 3171 | - **A CLI handler that writes a file uses `fsio.atomic_write`, never a |
| 135 | Code rules | CLI | 1184 | `src/codebugs/CLAUDE.md` 1000 + справка `docs/claude-md-rationale/` 184 | - **Where `init` creates is decided by the CHANNEL, not by the fact th |
| 136 | Architecture migration ( | — | 39 | корень `CLAUDE.md` 39 | ## Architecture migration (in progress) |
| 137 | Architecture migration ( | — | 433 | справка `docs/claude-md-rationale/` 343 + корень `CLAUDE.md` 90 |  We are migrating toward a plugin architecture in phases. Query with ` |
| 138 | Architecture migration ( | — | 183 | корень `CLAUDE.md` 183 | - New domain modules must call `register_schema()`, `register_tool_pro |
| 139 | Architecture migration ( | — | 103 | `src/codebugs/CLAUDE.md` 103 | - Add the new module import to `_ensure_modules_loaded()` in `db.py` ( |
| 140 | Architecture migration ( | — | 139 | `src/codebugs/CLAUDE.md` 139 | - Add the new module's mode slug to `SERVER_NAMES` (`server.py`) and t |
| 141 | Architecture migration ( | — | 78 | `src/codebugs/CLAUDE.md` 78 | - Prefer self-contained modules that register themselves over central  |
| 142 | Embeddings | — | 13 | корень `CLAUDE.md` 13 | ## Embeddings |
| 143 | Embeddings | — | 13001 | `src/codebugs/CLAUDE.md` 11687 + справка `docs/claude-md-rationale/` 950 + корень `CLAUDE.md` 364 |  `embeddings.py` stores a vector per requirement and answers similarit |
| 144 | Claims module | — | 16 | корень `CLAUDE.md` 16 | ## Claims module |
| 145 | Claims module | — | 479 | `src/codebugs/CLAUDE.md` 331 + корень `CLAUDE.md` 148 |  `claims.py` answers "who currently holds this entity" for findings an |
| 146 | Claims module | — | 501 | корень `CLAUDE.md` 395 + `src/codebugs/CLAUDE.md` 106 | - **Outcomes, not booleans**: `claim` → `claimed | already_mine | held |
| 147 | Claims module | — | 225 | корень `CLAUDE.md` 225 | - **Ownership is the triple** `(holder, holder_kind, holder_repo)`, co |
| 148 | Claims module | — | 151 | `src/codebugs/CLAUDE.md` 151 | - **The discriminator is `touch_count`, never a timestamp.** `utc_now( |
| 149 | Claims module | — | 636 | `src/codebugs/CLAUDE.md` 636 | - **Two layers.** `_claim_core` / `_release_core` emit statements and  |
| 150 | Claims module | — | 286 | `src/codebugs/CLAUDE.md` 286 | - **Projection is declarative**: `EntityKind.busy_status` (`in_progres |
| 151 | Claims module | — | 3263 | корень `CLAUDE.md` 2705 + справка `docs/claude-md-rationale/` 558 | - **Exit codes are the API for shell callers**: `0` proceed, `1` error |
| 152 | Claims module | — | 2325 | корень `CLAUDE.md` 1743 + справка `docs/claude-md-rationale/` 582 | - **Adoption**: autosorter's `worktree-setup.sh` claims every card in  |
| 153 | Claims module | — | 248 | `src/codebugs/CLAUDE.md` 248 | - Deferred by design, not forgotten: `steal`, claim history queries, a |
| 154 | Milestones module | — | 20 | корень `CLAUDE.md` 20 | ## Milestones module |
| 155 | Milestones module | — | 291 | корень `CLAUDE.md` 149 + `src/codebugs/CLAUDE.md` 142 |  Releases ("release/1.1") and standing streams ("stream/triage", "stre |
| 156 | Milestones module | — | 191 | `src/codebugs/CLAUDE.md` 191 | 1. **Foundation** — milestone & item CRUD, audit log, auto-routing eve |
| 157 | Milestones module | — | 562 | `src/codebugs/CLAUDE.md` 562 | 2. **Triage + pull** — `triage_inbox` / `triage_dismiss` / `triage_pro |
| 158 | Milestones module | — | 825 | `src/codebugs/CLAUDE.md` 825 | 3. **Close gate + branch tracking** — `mark_branch_only(item, branch)` |
