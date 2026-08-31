# Rationale — architecture

Biography for the corresponding rules in `CLAUDE.md`: review rounds, reproduced incidents,
rejected forms and the measurements a decision was made on. **No rule lives here.** A line
in this file that reads as an instruction is a defect, and its place is the rules layer.

---

### Known architectural debt — CB-26, CB-31, CB-45 {#известный-архитектурный-долг}

**Justifies the rules** in `CLAUDE.md` → `## Architecture` → `### Known architectural debt`.

**Two entries that outlived the code they described.** The staleness/provenance entry claimed the
logic still sat in `db.py` long after it had moved (CB-4), and the `blockers.py` entry described a private `_row_to_dict` reach that no longer existed anywhere in the package (CB-5).

**Where the resolver seam's three enforcement clauses came from.** The refusal to run outside an open
transaction, the detection of a resolver that closed the caller's transaction, and the guarded
`ROLLBACK TO` cleanup were all three cross-model review findings. The per-call NONCE in the savepoint
name came from a Codex diff review, which showed that index-only naming let a resolver commit and
recreate the runner's savepoint by its predictable name.

**The CB-26 measurement.** 19 of 23 open `stream/triage` rows pointed at terminal findings, so `triage_inbox` was about 83% stale. **What CB-31's first implementation broke.** It built the subquery inside `milestones/`, which reached into another module's tables, bypassed the `readable_cols` allowlist, and carried a `# noqa: S608` justified by validation in a file it did not own — three of this document's own rules,
broken by one function.

**Why the differential test's two odd rows are the whole of it.** Measured: the realistic NULL-unsafe
mutant is caught by the `external`-pointing-at-a-terminal-finding row and the `bug`-with-a-missing-
source row, and by nothing else — so an "externals are covered" fixture whose external points at a
*live* finding proves nothing.

**Cost of the per-row predicate, for the record.** `source_is_terminal` ran a `sqlite_master` probe plus a status `SELECT` for every candidate row, and per-bucket construction of the replacement would have added eight `sqlite_master` reads inside the exclusive-lock hold.

## Что в этом файле, и чего в нём нет

**Что в этом файле.** Обоснования правил из корневого `CLAUDE.md`: почему правило появилось, какой
инцидент его породил, что показали раунды состязательного ревю, какие формы были отвергнуты и по
какому замеру. С Т-131 сюда же переехала операционная глубина — устройство сторожей и хуков,
пределы алярмов, внутренности гейтов.

**Чего в этом файле НЕТ, и это важнее.** Здесь нет ни одного правила, которое нужно знать до начала
работы. Всё такое осталось в корневом `CLAUDE.md`, потому что этот файл не впрыскивается в сессию —
его открывает только тот, кого сюда послали. Если ты ищешь, как завести рабочее дерево, что значит
код отказа или что можно коммитить на `main`, — тебе не сюда, а в корень.

**Кто сюда ходит.** Тот, кто правит соответствующую подсистему, — и тот, кто собирается ослабить
правило и обязан сперва узнать, чем за него заплатили.

---

# Перенесено из корня юнитом Т-131

## Architecture

This line used to claim "~40 lines"; it was 159 before that change and is larger now, so the count is dropped rather than re-guessed

## Architecture / Known architectural debt

- ~~`blockers.py` cross-module reach into private `_row_to_dict`~~ — **resolved.** `blockers.py` calls the public `db.row_to_dict()` (`blockers.py:87`, `:307`, `:442`) and does not reach into `reqs` at all; no private `_row_to_dict` exists anywhere in the package (CB-5).

- **A VIEW was rejected for a measured reason, not the obvious one.** The obvious objection — a view's DDL would hardcode the terminal sets — is false; it could be regenerated from `kind.terminal` on every schema init. The real one: `CREATE VIEW` over a missing source table **succeeds**, and the first `SELECT` from it raises `no such table`. A view therefore fails **closed, with a crash**, for exactly the raw-connection callers this design must keep working.

## Architecture migration (in progress)

Query with `reqs_query --section "Architecture Migration"` or MCP tool `reqs_query(section="Architecture Migration")` for the full plan (ARCH-001 through ARCH-005).

**All phases complete**: schema registry (ARCH-001) -> tool registration (ARCH-002) -> entity types (ARCH-003) -> CLI unification (ARCH-004) -> embedding separation (ARCH-005).
