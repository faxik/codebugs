# UNIT Т-21 — CB-127: экспонировать `grouping.py` как MCP-тулы и CLI-вербы

Бриф юнита по SPEC-planner-cascade §12 (полный формат). Уровень (3) → (4).
Ветка `feature/cb-127-expose-grouping`, база `a6fed9a`. Дата: 2026-08-22.
Входящий хендофф: `.claude/plans/L3-BRIEF-DIR-2-T21-expose-grouping-2026-08-22.md`.
Образцы: `UNIT-T16-CB-123-recent-closed.md`, `src/codebugs/similarity.py` (хвост файла),
коммит `bc44b2f` (relations: три жёстко закодированных списка + пин-тест).

## unit

CB-127 — единственная карта юнита. Клейм взят ХАРНЕСОМ из имени ветки (Э-4), держатель
`feature/cb-127-expose-grouping`, kind `branch`, repo `/home/faxik/w/codebugs`.
CB-47 (потребитель, скилл `batch-codebugs`) в этом юните НЕ трогается.

## intent (замысел, не задание)

Три read-only отчёта `grouping.py` — `citation_report`, `tag_report`, `filing_report` —
отгружены (CHANGELOG, строка «`grouping.py` — the three grouping axes the tracker stored
but could not…»), но недостижимы ни из MCP, ни из CLI. Потребитель — сам каскад:
стратсессии и батч-разбор бэклога, которым сегодня приходится читать кросс-ссылки и теги
руками. Замысел: ОДИН домен-функционал, достижимый с ДВУХ поверхностей тонкими обёртками,
без переписывания логики отчётов, без единой новой строки SQL, с честными оговорками
модуля в описании каждого тула (CB-73: клиент видит докстринг как Markdown).

## premises[] (fresh, добыто 2026-08-22 на `a6fed9a`; якорь — ГРЕПАЕМАЯ цитата)

| Утверждение брифа (2) | Вердикт | Грепаемая улика |
|---|---|---|
| `grouping.py` — 641 строка, три отчёта | держится | `wc -l` = 641; `def citation_report(`, `def tag_report(`, `def filing_report(` |
| нет `register_tools`/`register_cli` | держится | `grep -n register_ src/codebugs/grouping.py` пуст |
| нет в `db._ensure_modules_loaded` | держится | список `bench, blockers, claims, findings, merge, milestones, provenance, relations, reqs, similarity, sweep` |
| нет в `server.SERVER_NAMES` | держится | последние ключи `"similarity": "codesimilarity"`, `"relations": "coderelations"`, `"all"` |
| нет в `choices=[…]` флага `--mode` в `cli.main` | держится | `…"claims", "similarity", "relations", "all"]` |
| **«ни один файл вне `tests/test_grouping.py` его не импортирует»** | **ОПРОВЕРГНУТО** | `src/codebugs/similarity.py`: `from codebugs.grouping import DSU` (+ `tests/test_similarity.py`). Следствие ниже, в preflight п.1 |
| ноль SQL в модуле | держится | `grep -n 'execute(' src/codebugs/grouping.py` пуст |
| домен-функции поднимают `ValueError` на отрицательных лимитах | держится | `raise ValueError(f"{name} must be >= 0, got {limit}")` ×3, `hub_degree must be >= 0 or None` |
| отчёты используют `is_vocabulary_filter_active` | держится | `_population`: `if is_vocabulary_filter_active(status):`; сентинел `"all"` type-pinned через `str.__eq__` |
| `parse_meta`/`parse_tags` толерантны | держится | `findings.parse_meta`: `except (TypeError, ValueError): return {}`; `parse_tags`: `except (TypeError, ValueError): return []` + `if not isinstance(tags, list): return []` |
| grouping не импортирует `db` | держится (важно для формы) | `grep -c 'codebugs.db\|import db' src/codebugs/grouping.py` = 0 — регистрация ТРЕБУЕТ нового `from codebugs import db` |

Сигнатуры домен-функций (keyword-only после `conn`), которые обёртки повторяют 1-в-1:

- `citation_report(conn, *, status=None, category=None, hub_degree=DEFAULT_HUB_DEGREE, component_limit=None, member_limit=None, anchor_limit=25, orphan_limit=50)`
- `tag_report(conn, *, status=None, category=None, min_pair_count=2, tag_limit=None, pair_limit=50)`
- `filing_report(conn, *, status=None, category=None, lineage_limit=None, event_limit=None)`

Ключи ответов (для табличного рендера): citations — `populations, hub_degree,
rows_considered, citations_total, edges_total, self_references, dangling_total, components,
components_total, cards_in_components, hubs, anchors, orphans…`; tags — `populations,
rows_considered, rows_untagged, tags, tags_total, tag_applications, pairs, pairs_total,
variants, variants_total`; filing — `populations, rows_considered, universe_rows, lineages,
lineages_total, cards_in_lineages, unresolved_refs, unresolved_total, events, events_total,
event_keys, lineage_keys`. Исполнитель берёт точные формы из `return {…}` в коде.

## preflight (вердикты 5 проверок §4)

1. **Процитированные предпосылки существуют и говорят то, что утверждает бриф — ДЕРЖАТСЯ
   10/11, ОДНА ОПРОВЕРГНУТА.** `similarity.py` импортирует `DSU` из `grouping`, значит
   grouping УЖЕ загружается транзитивно при любом `--mode`, в котором грузится similarity
   (а `_ensure_modules_loaded` грузит все). Два следствия, оба не блокируют: (а) ЦИКЛ
   ИМПОРТА ПРОВЕРЕН ЗАРАНЕЕ — `PYTHONPATH=src uv run --extra dev python -c "import
   codebugs.grouping; import codebugs.db as d; d._ensure_modules_loaded()"` → ок на main;
   после добавления `from codebugs import db` в grouping форма становится байт-в-байт
   формой similarity (`grouping → findings → db`, `db` не импортирует домен на верхнем
   уровне) — исполнитель ПЕРЕМЕРЯЕТ той же командой из worktree и пишет вердикт в слот
   эскалаций; (б) модульная регистрация `db.register_tool_provider("grouping", …)` сработает
   и при `--mode similarity`, но фильтр провайдеров — по ИМЕНИ (`db.get_tool_providers`:
   `[p for p in _tool_providers if p.name == mode]`), так что изоляция режимов не
   нарушается. Буква брифа поправлена под замысел, замысел не сдвинулся.
2. **Предпосылки против СХЕМЫ и КОНФИГА — ДЕРЖАТСЯ.** Схема не меняется (ноль SQL,
   ноль таблиц). Конфиг: golden сравнивается двумя тестами (`test_schema_matches_golden`,
   `test_golden_is_already_normalized`) — регенерировать ТОЛЬКО указанной командой из
   worktree. `ruff 0.15.7` — пин из CLAUDE.md.
3. **Названный файл — действительно место фикса — ДЕРЖИТСЯ.** Регистрация живёт в конце
   `grouping.py` (как `similarity.py`: `db.register_tool_provider("similarity",
   register_tools)` / `db.register_cli_provider(...)`). Три списка — единственная правка
   в `db.py`/`server.py`/`cli.py` (файлы DIR-1).
4. **Пересечение с in-flight — НЕТ.** Единственная живая ветка DIR-1 —
   `fix/t21-arm-commit-msg-hook`, трогает только `tests/test_worktree_harness.py`. Golden
   никем параллельно не регенерируется. Митигация на случай сдвига main остаётся:
   форвард-мердж → реген golden из комбинированного дерева → повтор finish с тем же
   `--merge-msg`.
5. **Повторный прогон для залежавшегося брифа — N/A**: бриф (2) и юнит-бриф в один день.

**Аксессор достаточен (критерий 3):** все параметры трёх отчётов — `status`/`category`
(идут в `_population` → `findings.grouping_candidates(category=, statuses=)`) и лимиты
(режутся в Python). Ни одному параметру новый SELECT не нужен. Эскалация по этому пункту
НЕ возникает.

**Вердикт по JSONDecodeError-арме:** НЕ НУЖНА. Оба парсера stored-data в grouping —
`parse_meta` и `parse_tags` — толерантны (цитаты в таблице выше); `json` в grouping.py не
импортируется вовсе (`grep -n json` даёт только четыре вызова `parse_meta`/`parse_tags`).
Значит stored-data `JSONDecodeError` структурно не достигает CLI-кадра, и арма
утверждала бы несуществующую опасность — ровно довод из комментария
`_cmd_similarity_report`. Хэндлеры ловят `(KeyError, ValueError)` и несут тот же
комментарий «no stored-data JSONDecodeError can surface here (parse_meta/parse_tags are
tolerant)».

## prescription (ВЫБРАННАЯ ФОРМА — решение (3))

Форма (2) принята целиком; расхождение с брифом одно и уже названо (премисса про
импортёров). Дополнительно зафиксировано одно уточнение буквы: бриф говорит «только
регистрация в конце файла» — фактически нужен ещё ОДИН новый top-level импорт
`from codebugs import db` (и `import json`/`sys` внутри `register_cli`, как в similarity),
без которого регистрация невозможна. Это буква, а не замысел.

### Что именно построить

1. **`src/codebugs/grouping.py`, хвост файла**, по образцу similarity:
   - `register_tools(mcp, conn_factory)` с тремя `@mcp.tool()`:
     `grouping_citations`, `grouping_tags`, `grouping_filing`. Параметры = keyword-only
     сигнатуры функций ОДИН-В-ОДИН (имена, типы, дефолты: `hub_degree: int | None =
     DEFAULT_HUB_DEGREE`, `anchor_limit: int = 25`, …). Тело — `with conn_factory() as
     conn: return <report>(conn, **все параметры)`. Никакого `if status:`.
   - Докстринги (клиент видит как Markdown, CB-73), по одной честной оговорке из
     докстринга модуля/функции, по смыслу: citations — «READ-ONLY, an ANNOTATION of what
     people already wrote — no link here is inferred» + что такое hub (узел со степенью >
     `hub_degree` не передаёт связность, репортится как ANCHOR с цитирующими);
     tags — Jaccard рядом с raw count (почему: один 390-карточный тег ранжирует свои пары
     первыми при любой слабости связи) и что `variants` покрывает теги И категории;
     filing — «LINEAGE IS TRAVERSED, NOT GROUPED», ссылки резолвятся против ВСЕГО
     трекера, а не популяции. Блок `Args:` как в similarity.
   - `register_cli(sub, commands)`: вербы `grouping-citations`, `grouping-tags`,
     `grouping-filing`; аргументы — те же имена через дефис (`--status`, `--category`,
     `--hub-degree` (dest `hub_degree`, `type=int`, default `DEFAULT_HUB_DEGREE`),
     `--component-limit`, `--member-limit`, `--anchor-limit`, `--orphan-limit`,
     `--min-pair-count`, `--tag-limit`, `--pair-limit`, `--lineage-limit`,
     `--event-limit`) и `--json` (`dest="as_json"`). `help=` каждого верба несёт ту же
     оговорку кратко. **`hub_degree=None` с CLI:** у функции `None` = «отключить хабы»;
     argparse `type=int` не даёт `None` — допускается `--no-hubs` (store_true →
     `hub_degree=None`) ИЛИ `--hub-degree` с `type=` функцией, принимающей `none`.
     Исполнитель выбирает, документирует в `help`, и тест покрывает, что `None` достижим
     с CLI (иначе это дыра CLI против MCP, которую бриф запрещает).
   - Хэндлеры `_cmd_grouping_citations/_tags/_filing`: `conn = db.connect()`; `try: report
     = …(conn, …) except (KeyError, ValueError) as e: print(f"Error: {e}",
     file=sys.stderr); sys.exit(1) finally: conn.close()`; `--json` → `json.dumps(report,
     indent=2)`; иначе — сводная строка (`populations=… rows=… …`) + таблицы через
     `codebugs.fmt.format_table` (компоненты/анчоры; теги/пары/варианты;
     линии/события) и явное `No citation components.` / `No tags.` / `No lineages.` на
     пустом отчёте — НЕ пустая строка. Усечение с подсказкой флага, как в
     `_cmd_similarity_report`.
   - В конце: `db.register_tool_provider("grouping", register_tools)`,
     `db.register_cli_provider("grouping", register_cli)`.
2. **Три списка** — по одной строке: `grouping,` в `db._ensure_modules_loaded` между
   `findings,` и `merge,`; `"grouping": "codegrouping",` в `server.SERVER_NAMES` перед
   `"all"`; `"grouping"` в `choices` перед `"relations"` в `cli.main`. Ничего больше в
   этих трёх файлах.
3. **Golden**: из worktree `PYTHONPATH=src uv run --extra dev python tests/dump_schema.py >
   tests/golden/mcp_schema.json`; дифф — ровно три добавленных тула, ничего не исчезло и
   не изменило описание (исполнитель прикладывает `git diff --stat` и перечень имён).
4. **CHANGELOG**: одна запись «exposed» под CB-127 со ссылкой на существующую запись о
   `grouping.py`.

## acceptance (тесты, обязанные упасть при мутации)

Файл: `tests/test_grouping_surface.py` (новый; `tests/test_grouping.py` не трогать, кроме
нужды). Мечаника MCP — как `tests/test_server.py::_server_with_middleware` (реальный
`MCPServer` + `install_strict_arguments` + `mcp.call_tool`); CLI — `cli.main()` in-process
с подменой `sys.argv` (как в `tests/test_findings.py`) и `capsys`.

1. **Тонкий проброс (критерий 1), ПОВЕДЕНЧЕСКИ, на каждую обёртку и каждый параметр:**
   monkeypatch'ем подменить `grouping.citation_report`/`tag_report`/`filing_report` на
   записывающую заглушку и утверждать, что MCP-вызов и CLI-вызов с НЕДЕФОЛТНЫМ значением
   каждого параметра доставляют ИМЕННО это значение в kwargs (например `hub_degree=7`,
   `min_pair_count=5`, `event_limit=3`). Это тест, который падает на мутации (а).
   Плюс структурный: `inspect.signature` каждой MCP-обёртки == набор keyword-only
   параметров функции (имена И дефолты); и ни один `def` внутри `register_tools`/
   `register_cli` не содержит `conn.execute`/`.execute(` (читать `inspect.getsource`).
2. **Три списка (критерий 2)** — `test_grouping_is_registered_in_all_three_hardcoded_lists`
   по образцу relations: `"grouping" in inspect.getsource(db._ensure_modules_loaded)`,
   `in server.SERVER_NAMES`, `in inspect.getsource(cli.main)`. Плюс изоляция режима:
   `db.get_tool_providers(mode="grouping")` даёт ровно одного провайдера с именем
   `grouping`, и зарегистрированные на `MCPServer` тулы в этом режиме — ровно
   `{grouping_citations, grouping_tags, grouping_filing}`; в `mode="all"` они тоже есть.
   Это тест мутации (б).
3. **Ноль SQL (критерий 3)** — структурный: `"execute(" not in inspect.getsource(grouping)`
   (весь модуль).
4. **Строгие аргументы (ловушка 2)** — `tools/call` на `grouping_citations` с
   `hub_degre=3` (опечатка) → ошибка, не успех.
5. **Пустая популяция (ловушка 4)** — на пустом трекере каждый MCP-тул возвращает отчёт с
   нулями/пустыми списками (ключ `populations` присутствует), CLI печатает явное «No …».
6. **Ошибки** — `--hub-degree -1` / `--min-pair-count -1` → одна строка в stderr, exit 1,
   не трейсбек; MCP с отрицательным лимитом → исключение (пропагирует).
7. **`None` достижим с CLI для `hub_degree`** (через выбранный механизм) — заглушка
   получает `hub_degree=None`.
8. **Реальный прогон** хотя бы одного сценария на живых данных через MCP (две карты с
   взаимной ссылкой → `components_total == 1`), чтобы проброс был доказан не только
   заглушкой.
9. **Golden** — `test_schema_matches_golden` и `test_golden_is_already_normalized`
   зелёные после регенерации.

**Мутационная проба (§13 п.2) — ТОЛЬКО во ВРЕМЕННОМ worktree** (`git worktree add <tmp>
<sha>` → мутация → прогон → `remove --force`). Минимум две, обе обязаны дать КРАСНОЕ:
(М1) обёртка `grouping_citations` перестаёт пробрасывать `hub_degree` → падает тест п.1;
(М2) `grouping` убран из одного из трёх списков → падает тест п.2.

## Границы

- Правим: `src/codebugs/grouping.py` (хвост + один импорт `db`), новый
  `tests/test_grouping_surface.py`, `tests/golden/mcp_schema.json`, `CHANGELOG.md`, по
  одной строке в `db.py`/`server.py`/`cli.py`.
- НЕ правим: `findings.py`, `similarity.py`, `tools/`, `CLAUDE.md`, `types.py`, `fmt.py`,
  схему БД. Не переписываем логику отчётов и их докстринги. CB-47 не трогаем.
- Любая нужда в новом SELECT — эскалация, не код.

## Протокол

- TDD: сначала красный коммит с тестами, потом реализация. Ветка начинается с красного —
  поэтому `--merge-msg` при финише ОБЯЗАТЕЛЕН (К-4).
- Прогон ИЗ worktree: `uv run --extra dev python -m pytest tests/ -q`; линт
  `uv run --extra dev ruff check src/ tests/`.
- К-1: на main не коммитить ничего. Всё — на ветке.
- Исполнитель возвращает: список коммитов, имена тестов, golden-дифф (имена добавленных
  тулов), вердикт цикла импорта (команда + вывод), и СЛОТ ЭСКАЛАЦИЙ (обязателен, пустой —
  явно).

## Факт приёмки (3)←(4)

**Т-21 ПРИНЯТ на коммите `35f2285` (голова ветки `feature/cb-127-expose-grouping`), 2026-08-22.**
Неизменяемый факт. Цитаты грепаемые, номера строк не приводятся.

Контракт §13, семь пунктов:

1. **Дифф прочитан против ЗАМЫСЛА — ПРИНЯТО.** Один домен-функционал с двух поверхностей:
   MCP `grouping_citations`/`grouping_tags`/`grouping_filing` и CLI `grouping-citations`/
   `grouping-tags`/`grouping-filing`. Каждая обёртка — `with conn_factory() as conn: return
   <report>(conn, …все параметры…)`; сигнатуры повторяют keyword-only сигнатуры функций
   один-в-один (пинит `test_wrapper_signature_equals_domain_keyword_signature`). Оговорки
   стоят в докстрингах: citations — «READ-ONLY, and an ANNOTATION of what people already
   wrote — no link here is inferred» + определение anchor/hub; tags — «Co-occurrence carries
   Jaccard beside the raw count» + «`variants` spans tags AND categories»; filing —
   «LINEAGE IS TRAVERSED, NOT GROUPED» + «resolve against EVERY card in the tracker, not
   just the population». Ни одной строки логики отчёта в обёртках, ни одного `execute(`
   во всём модуле (`test_module_issues_no_sql`, `test_wrappers_and_cli_issue_no_sql`).
2. **Мутационная проба приёмщика — во ВРЕМЕННОМ worktree на `35f2285`, три мутации, все
   КРАСНЫЕ**: (М1) `hub_degree=hub_degree` → `hub_degree=DEFAULT_HUB_DEGREE` в обёртке
   `grouping_citations` → `FAILED …test_mcp_delivers_every_parameter_verbatim[grouping_citations]`;
   (М2) `"grouping"` убран из `choices` в `cli.main` (другой список, чем проверял
   исполнитель — он убирал из `SERVER_NAMES`) → `FAILED
   …test_grouping_is_registered_in_all_three_hardcoded_lists`; (М3, независимая) CLI-хэндлер
   `grouping-tags` перестаёт пробрасывать `min_pair_count` → `FAILED
   …test_cli_delivers_every_parameter_verbatim[grouping-tags]`. Worktree удалён
   (`git worktree list` без `mut-t21`).
3. **Три списка — ровно по одной строке**: `db.py` `+            grouping,`; `server.py`
   `+    "grouping": "codegrouping",`; `cli.py` — `"similarity", "grouping", "relations"`.
   Больше в этих файлах ничего (проверено `git diff a6fed9a..HEAD`).
4. **Golden** — `1 file changed, 201 insertions(+)`, ноль удалений, добавлены ровно
   `grouping_citations`, `grouping_filing`, `grouping_tags`; `test_schema_matches_golden`
   и `test_golden_is_already_normalized` зелёные в полном прогоне исполнителя
   (`1916 passed`), ruff `All checks passed!`.
5. **JSONDecodeError-арма** — не нужна, вердикт preflight подтверждён кодом: хэндлеры
   ловят `(KeyError, ValueError)` и несут комментарий «no stored-data JSONDecodeError can
   surface here». **Цикл импорта** — перемерен исполнителем из worktree после добавления
   `from codebugs import db`: `ok`.
6. **Заместитель §13 п.6 — `tools/worktree-finish.sh`** с обязательным `--merge-msg`
   (ветка начинается с красного коммита `94925cf`).
7. **CB-127 → `fixed` с `append_note`** после мерджа (SHA — в возврате (2)).

**Расхождения с брифом, принятые как буква, не замысел:** (а) `_run` — общий
try/except/finally трёх CLI-хэндлеров вынесен в локальную функцию (семантика та же, что в
`_cmd_similarity_report`); (б) `hub_degree=None` с CLI — через `--hub-degree none`
(`type=_hub_degree_arg`), один флаг = один параметр домена; покрыто
`test_cli_can_disable_hubs`; (в) премисса брифа (2) «никто кроме test_grouping не
импортирует grouping» была ложной (`similarity.py: from codebugs.grouping import DSU`) —
без последствий, см. preflight п.1.

## Эскалации (§13 п.4 — слот обязателен)

**Блокирующих эскалаций НЕТ.** Слот исполнителя (4), ДОСЛОВНО:

> - **Новый SELECT не понадобился** — все параметры трёх отчётов идут через `findings.grouping_candidates` и Python-срезы; предпосылка брифа подтвердилась, эскалации по этому пункту нет.
> - **Цикл импорта**: перемерен из worktree после добавления `from codebugs import db` — `ok` (см. выше). Не блокирует.
> - Отклонение от буквы брифа — одно, непринципиальное: в CLI-хэндлерах общий `try/except/finally` вынесен в локальную функцию `_run` (три хэндлера делили бы байт-в-байт одинаковый блок); семантика (`except (KeyError, ValueError)` → stderr + `sys.exit(1)`, `finally: conn.close()`) та же, что в `_cmd_similarity_report`. Замысел не сдвинут.
> - Иных эскалаций нет.

Наблюдение приёмщика, не блокирующее: `test_module_issues_no_sql` зелёный с обеих сторон
любой мутации этого юнита (ратчет на будущее добавление SQL) — исполнитель сам назвал это
в сообщении красного коммита; вакуозным не считается, потому что пинит свойство, которое
юнит обязан СОХРАНИТЬ, и сказано это явно.
