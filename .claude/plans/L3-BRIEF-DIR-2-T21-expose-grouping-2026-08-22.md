# L3-BRIEF Т-21 — CB-127: экспонировать `grouping.py` (citation / tag / filing) как MCP-тулы и CLI-вербы

Направление DIR-2 «Identity находок», уровень (2) → менеджер (3). Дата 2026-08-22.
Карта юнита: **CB-127** (заведена (2) сегодня). Карта-потребитель: **CB-47** (скилл
`batch-codebugs`) — её судьбу решает (2) ПОСЛЕ посадки, менеджер её не трогает.
Ратификация владельца: 2026-08-22, `PAIN-REGISTRY-codebugs.md`, блок «Пакет DIR-2 (сессия 3)»
п.1 — «grouping ЭКСПОНИРОВАТЬ». Механический юнит, без нерешённых решений владельца.

Образцы (читать до preflight): `UNIT-T16-CB-123-recent-closed.md` и
`L3-BRIEF-DIR-2-T16-recent-closed-2026-08-21.md` (экспонирование готовой способности:
MCP-тул + CLI-верб поверх домен-функции), `similarity.py` (первый самореrистрирующийся
не-доменный модуль с нулём SQL — ближайший родственник grouping), коммит `bc44b2f`
(`relations`: как новый модуль входит в три жёстко закодированных списка и как это
пинится тестом `test_relations_is_registered_in_all_three_hardcoded_lists`).

## Тройка П-Б

**Замысел.** 641 строка `src/codebugs/grouping.py` — три read-only отчёта
(`citation_report`, `tag_report`, `filing_report`), описанные в CHANGELOG как отгруженная
фича, — недостижимы ни из MCP, ни из CLI: у модуля нет `register_tools`/`register_cli`, его
нет в `db._ensure_modules_loaded`, `server.SERVER_NAMES` и в `choices=[…]` флага `--mode` в
`cli.main`, и ни один файл вне `tests/test_grouping.py` его не импортирует (проверено
(2) грепом 2026-08-22; ты перепроверяешь в preflight). Потребитель — сам каскад:
стратсессии и батч-разбор бэклога (скилл `batch-codebugs`, CB-47), которым сегодня
приходится читать кросс-ссылки и теги руками.

**Гипотеза.** Ровно по образцу `recent` (Т-16) и `relations`: тонкие MCP-обёртки и
CLI-вербы ПОВЕРХ готовых функций, без переписывания логики отчётов, без единой новой
строки SQL где бы то ни было (контракт grouping — ноль SQL, все строки через
`findings.grouping_candidates`), со слагом `grouping` во всех трёх списках и
регенерированным golden.

**Критерий приёмки (из пакета куратора, дословно по смыслу):**
1. Оба хода — CLI и MCP — вызывают ОДИН домен-функционал: каждая MCP-обёртка и каждый
   CLI-хэндлер — тонкий проброс аргументов в `citation_report`/`tag_report`/`filing_report`;
   никакой логики отчёта в обёртках (структурный тест это пинит: ни один `def` внутри
   `register_tools`/`register_cli` не содержит `conn.execute`).
2. `codebugs --mode grouping` (CLI) и `codebugs-server --mode grouping` изолируют модуль:
   в этом режиме видны только его тулы/вербы; в `--mode all` они тоже есть. Пин-тест по
   образцу relations: слаг присутствует в `inspect.getsource(db._ensure_modules_loaded)`,
   в `server.SERVER_NAMES`, в `inspect.getsource(cli.main)`.
3. Ноль новых SQL вне модуля — и внутри модуля тоже (там их ноль сегодня; `grep -n
   'execute(' src/codebugs/grouping.py` пуст до и после). Если для какого-то параметра
   отчёта аксессора `grouping_candidates` не хватает — это ЭСКАЛАЦИЯ, а не новый SELECT.
4. Golden `tests/golden/mcp_schema.json` регенерирован ИЗ WORKTREE:
   `PYTHONPATH=src uv run --extra dev python tests/dump_schema.py > tests/golden/mcp_schema.json`;
   дифф golden состоит ТОЛЬКО из добавленных тулов grouping (состав диффа — в возврате).
5. CB-47 в этом юните НЕ закрывается и не правится — (2) решает после посадки.

## Форма (предписание (2) — ПРЕДПОСЫЛКА, добывай из кода и опровергай, если код против)

- **Три MCP-тула, не один с осью `kind=`.** Один тул с объединением аргументов трёх
  отчётов — это ровно CB-28: объявленный аргумент (`hub_degree`), который ветка
  `kind="tags"` никогда не пробрасывает, и «маршрутизация — не оправдание». По одному тулу
  на функцию: `grouping_citations`, `grouping_tags`, `grouping_filing` — префикс домена
  обязателен (правило CLAUDE.md «MCP tool functions are prefixed with the domain»).
  Сигнатуры обёрток = keyword-only сигнатуры функций один-в-один (`status`, `category`,
  лимиты, `hub_degree`, `min_pair_count`); никаких новых параметров и никаких
  выброшенных. Параметр, который обёртка объявляет и не передаёт, валит критерий 1.
- **Три CLI-верба** `grouping-citations`, `grouping-tags`, `grouping-filing` (дефисная
  форма как у `similarity-report`), хэндлеры `_cmd_grouping_<action>`, каждый с `--json`
  и табличным рендером через `codebugs.fmt.format_table` (образец —
  `_cmd_similarity_report`). CLI-аргументы — те же имена через дефис; дыра CLI против MCP
  здесь НЕ допускается (CB-6 — это про старые вербы; новая поверхность рождается полной).
- **Ошибки.** Домен-функции поднимают `ValueError` на отрицательных лимитах (есть в
  коде). CLI ловит `(KeyError, ValueError)` → stderr + `sys.exit(1)`. Нужна ли арма
  `json.JSONDecodeError` ПЕРЕД ней — ПРОВЕРЬ: `similarity` её не несёт, потому что
  `parse_meta` толерантна; grouping тоже импортирует `parse_meta`/`parse_tags` — убедись,
  что оба толерантны, и запиши вердикт в юнит-бриф. Если хотя бы один строг — арма
  обязательна в порядке, который пинит `TestRetriageCliContract`.
- **Три списка.** По одной строке-перечислению в `db._ensure_modules_loaded`
  (`grouping,` в алфавитном месте), `server.SERVER_NAMES["grouping"] = "codegrouping"`,
  `cli.py` choices `"grouping"` перед `"relations"`. **Это ЕДИНСТВЕННОЕ, что ты трогаешь в
  `db.py`/`server.py`/`cli.py`** — файлы DIR-1; любое другое изменение там — эскалация.
  Дополнительно проверь: `grouping` импортирует `codebugs.findings` на верхнем уровне —
  убедись, что добавление его в `_ensure_modules_loaded` не создаёт цикла импорта
  (`findings` → `db` → `_ensure_modules_loaded` → `grouping` → `findings`). `similarity`
  живёт с той же формой, так что ожидаю «цикла нет» — но замерь (`python -c "import
  codebugs.db as d; d._ensure_modules_loaded()"` из worktree с `PYTHONPATH=src`).
- **Описания тулов** — докстринги обёрток, которые клиент видит как Markdown (CB-73):
  каждый обязан нести одну честную оговорку из докстринга модуля — для citations:
  «ANNOTATION of what people already wrote — no link here is inferred» и что такое
  anchor/hub; для tags: Jaccard рядом с raw count и что `variants` охватывает теги И
  категории; для filing: «lineage is TRAVERSED, not grouped», ссылки резолвятся против
  ВСЕГО трекера, а не популяции. Не переписывай модульные докстринги — цитируй по смыслу.
- **CHANGELOG** — одна запись «exposed» под CB-127 со ссылкой на существующую запись о
  `grouping.py`.

## Ловушки (из истории этого модуля и репозитория — неси с собой)

1. **Golden регенерируется из worktree с `PYTHONPATH=src`**, иначе голый `python` дойдёт до
   `codebugs` через editable-install main и снимет НЕ ТО дерево (правило MCP-регистрации
   CLAUDE.md). Дифф golden смотри глазами: должны добавиться ровно три тула, ничего не
   должно исчезнуть или изменить описание.
2. **Неизвестные аргументы отказываются, не игнорируются** (`install_strict_arguments`,
   CB-15) — поведенческий MCP-тест на `tools/call` с опечатанным именем аргумента
   должен дать ошибку, а не успех.
3. **«No filter» = `None` и `""`** для vocabulary-фильтров (`status`) — отчёты уже
   используют `is_vocabulary_filter_active`; обёртки не должны добавлять `if status:`.
4. **Пустой результат ≠ отсутствие способности.** Пустая популяция возвращает отчёт с
   нулями и пустыми списками — пусть тест это фиксирует; CLI на пустом отчёте печатает
   явное «No …», не пустую строку.
5. **Два тестовых модуля в одном файле findings.py параллельно не идут** (Т-17 и Т-16
   уже приземлены) — но **параллельно работает DIR-1** (пакет сессии 3: `tools/`,
   `_guards.sh`, CLAUDE.md, возможно `matrix.py`). Ты не трогаешь их файлы; при отказе
   finish'а на сдвинувшемся main — форвард-мердж, повторный реген golden из
   комбинированного дерева, повтор.
6. **Мутационная проба — во ВРЕМЕННОМ worktree** (`git worktree add <tmp> <sha>` → проба
   → `remove --force`). Минимум две мутации: (а) обёртка перестаёт пробрасывать один
   аргумент (например `hub_degree`) — тест должен упасть; (б) слаг убран из одного из трёх
   списков — пин-тест должен упасть.

## Границы, каноны, протокол

- Файлы: `src/codebugs/grouping.py` (только регистрация в конце файла, по образцу
  similarity), `tests/test_grouping.py` (+ при желании `tests/test_grouping_surface.py`),
  `tests/golden/mcp_schema.json`, `CHANGELOG.md`, по одной строке в `db.py`/`server.py`/
  `cli.py` (см. выше). НЕ трогать: `findings.py` (аксессор уже есть), `tools/`, `CLAUDE.md`
  (если правка CLAUDE.md кажется необходимой — опиши в эскалации, (2) сделает на main).
- **Э-4: клеймит ХАРНЕС.** `bash tools/worktree-setup.sh feature/cb-127-expose-grouping`
  берёт клейм CB-127 из имени ветки. Пре-клейм запрещён. Exit 3 → стоп и доклад.
- **К-1: на main НЕ коммитить ничего.** Факт своей приёмки и слот эскалаций исполнителя
  (ДОСЛОВНО, п.6 шаблона) пиши в юнит-бриф `UNIT-T21-CB-127-expose-grouping.md` НА
  ВЕТКЕ; строка в документе направления — только рукой (2) после вердикта.
- **К-4: `--merge-msg` ОБЯЗАТЕЛЕН**: `tools/worktree-finish.sh feature-cb-127-expose-grouping
  --merge-msg 'Merge feature/cb-127-expose-grouping: expose grouping reports as MCP tools and CLI verbs (CB-127)'`
  — ветка начнётся с красных тестов, иначе субъект мерджа назовёт TDD-коммит.
- **Урок цитирования:** в факте приёмки цитируй ГРЕПАЕМОЕ содержание, не номера строк.
- Исполнитель — свежий субагент (П-В), TDD; прогон ИЗ worktree `uv run --extra dev python
  -m pytest tests/ -q`; линт `uv run --extra dev ruff check src/ tests/` (ruff 0.15.7).
  Юнит-бриф §12 с пятью preflight-вердиктами — в worktree, коммит на ветке.
- Уровень (3) не будит исполнителя и не коммитит, пока исполнитель ещё работает в дереве
  (инцидент Т-15).

## Приёмка и возврат

§13 все семь (п.6 — заместитель `worktree-finish.sh`; п.7 — CB-127 в `fixed` после
мерджа с `append_note`, автоснимет branch-клейм). Возврат (2) — тройка: результат;
доказательства (мердж-SHA, имена тестов, обе мутации с выводом, пять вердиктов preflight,
ВЫБРАННАЯ ФОРМА и где она разошлась с предписанием, состав golden-диффа, вердикт по
JSONDecodeError-арме, вердикт по циклу импорта); эскалации — слот обязателен, пустой —
явно, слот исполнителя перенесён дословно.
