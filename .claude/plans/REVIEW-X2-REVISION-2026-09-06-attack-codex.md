## major_risks

1. **Карта retirement ошибочно считает milestones независимым кандидатом** | `REVISION...md:711-714` утверждает отсутствие live-зависимостей; `src/codebugs/milestones/__init__.py:713-732` регистрирует `auto_route` и status hooks, `src/codebugs/milestones/triage.py:23-57` автоматически меняет milestone-таблицы при добавлении finding, вызовы идут из `src/codebugs/findings.py:1471-1494,2754-2761` и `src/codebugs/reqs.py:299`; зависимость дополнительно проверяется вне milestone-suite в `tests/test_dedup.py:256,725` и `tests/test_findings.py:2163-2174` | Заморозка или удаление milestones изменит поведение основного findings/requirements workflow даже при нулевом использовании milestone MCP-tools. Центральная предпосылка П-8 опровергнута.

2. **«Заморозка» не определена и технически не изолирует модули** | П-8 в `REVISION...md:935-947` смешивает freeze, quarantine и delete; `src/codebugs/db.py:2266-2268` запускает все зарегистрированные schema callbacks при каждом подключении, а milestone hooks остаются зарегистрированными в `milestones/__init__.py:713-732`; CI продолжает `pytest tests/` в `.github/workflows/ci.yml:60-68`, локальный gate — `tools/worktree-finish.sh:779-794` | Пакет может формально выполнить критерий, не сократив runtime-, schema- или review-coupling. Для milestones такая «заморозка» ещё и сохраняет скрытые побочные эффекты.

3. **П-4 не разрывает импортное кольцо** | `src/codebugs/db.py:2117-2132` импортирует 14 доменных модулей; они импортируют `db`, а 11 также импортируют `domain_errors` из `cli`. AST-анализ текущих импортов дал `largest SCC = 25`; после удаления всех domain→`cli` рёбер — `largest SCC = 24` | Предложенный перенос helper лишь выталкивает `cli` из цикла. Кольцо `db ↔ domain modules` остаётся, а критерий «SCC заметно уменьшается» допускает фиктивный успех 25→24.

4. **С-2 ложно утверждает, что SDK validation boundary нельзя перехватить проектным wrapper** | `mcp/server/context.py:146-163` документирует middleware до lookup/validation/handler; `mcp/server/extension.py:96-156` допускает short-circuit через `intercept_tool_call`; проект уже использует такой перехват для raw arguments в `src/codebugs/server.py:418-461`. В SDK 2.0.0 сама валидация находится в `mcp/server/mcpserver/tools/base.py:123-181` и `mcp/server/mcpserver/func_metadata.py:72-100` | Отдельного formatter-hook в просмотренном SDK нет, но исходная невозможность из `REVISION...md:172-178` опровергнута. П-2 проектируется вокруг несуществующего ограничения и игнорирует уже имеющийся middleware seam.

5. **С-10 внутренне противоречива: foreign keys включаются, но только некоторыми миграционными путями** | Заголовок и тезис `REVISION...md:423-431` говорят, что FK не включаются ни на одном соединении, однако тот же документ признаёт обратное в `:444-450`; фактически `src/codebugs/findings.py:125,159` выполняет `PRAGMA foreign_keys=OFF/ON`. Свежая in-memory база после регистрации дала `PRAGMA foreign_keys = 0`, `17 tables`, `35 indexes`, `5 foreign keys` | Реальная проблема — зависимость режима FK от истории базы, а не безусловное отключение. П-10 `:968-985` повторяет неверную абсолютную предпосылку и не тестирует все пути открытия/миграции.

6. **С-7 неверно описывает текущий pin Ruff** | `pyproject.toml:32` действительно содержит незакреплённый `ruff`, CI использует `uvx ruff@0.15.7` в `.github/workflows/ci.yml:65`, но `uv.lock:674-677` уже фиксирует `ruff 0.15.7`, а локальный gate запускает именно lock-resolved окружение через `uv run --extra dev ruff` в `tools/worktree-finish.sh:786` | Сейчас расхождения версий между CI и guard нет. Есть риск будущего обновления lock, но формулировка «pinned only in CI text / not pinned where guard reads» фактически ложна.

7. **Retirement не имеет миграции данных, rollback и compatibility story** | П-8 `REVISION...md:935-947` разрешает удаление модулей, но не определяет судьбу 11 SQLite-таблиц, существующих MCP/CLI command names и данных; правила проекта требуют additive schema либо явную migration в `src/codebugs/CLAUDE.md:91-94` | Удаление регистраций оставит бесхозные таблицы либо потребует разрушительного DROP; удаление tools/commands является внешним breaking change. Нет версии удаления, deprecation window, экспорта, tombstone-ответов или восстановления.

8. **Пакеты объявлены независимее, чем позволяют пересечения файлов** | П-6 `REVISION...md:902-914` меняет bench/sweep tests, которые П-8 `:935-947` может quarantine/delete; П-9 `:949-966` меняет vocab/schema тех же retirement-кандидатов; П-10 затрагивает их FK; явные столкновения признаются только для П-1/П-4 и П-7/П-11 в `:1102-1112` | Параллельное выполнение создаст конфликтующие изменения и бессмысленный рефакторинг кода, который владелец затем решит удалить.

9. **Центральная теория о «трёх независимых списках одного правила» смешивает разные границы** | `src/codebugs/cli.py:16-55` обрабатывает domain `ValueError/KeyError`; `cli.py:312-341` обрабатывает connection/init errors; `src/codebugs/server.py:228-256` содержит объединённый MCP-набор. Сам документ признаёт различие scopes в `REVISION...md:145-150` | Наличие нескольких catch-наборов реально, но они отвечают на разные вопросы. Долг — отсутствие типизированной иерархии отказов и boundary translation, а не само наличие трёх перечислений.

10. **П-1 имеет нефальсифицируемый критерий полноты** | `REVISION...md:821-836` требует, чтобы «новый класс менялся ровно в одном месте» и guard доказывал все reachable refusals; при этом SQLite errors возникают вне доменной пары `ValueError/KeyError`, например необёрнутый `reqs add` в `src/codebugs/reqs.py:1001-1011` | Статический guard не может доказать полноту произвольных исключений без определённой иерархии и population rule. Пакет способен пройти ограниченный grep-тест, оставив реальные wire-level утечки.

11. **П-9 основан на неверном размере population и конфликтует с запретом SQL interpolation** | Документ заявляет `20 CHECK occurrences / 8 files` в `REVISION...md:482-484`; `rg 'CHECK.*IN' src/codebugs` даёт 20 совпадений в **9** файлах. П-9 `:955-959` предлагает собирать constraint из Python tuple, тогда как `CLAUDE.md:400-403` запрещает интерполировать значения в SQL; П-11 `:1002-1013` не добавляет это необходимое исключение | Acceptance не определяет полный набор vocabulary и предлагает механизм, формально нарушающий корневое правило.

12. **Usage evidence недостаточно воспроизводимо для удаления кода** | `PAIN-REGISTRY-codebugs.md:57-63` говорит о scratchpad scripts, а `REVISION...md:1211-1220` ссылается на временные `/tmp/...` artifacts; повторная проверка codesweep назначена только текстом в `REVISION...md:742` | У следующего ревьюера нет закреплённого запроса, временного окна, snapshot/hash и определения meaningful call. Решение об удалении тысяч строк нельзя независимо повторить.

## design_smells

1. **Документ и переданный контекст уже разошлись по версии** | Текущий документ сам говорит о 14 findings в `REVISION...md:117` и 11 packages в `:806-808`, тогда как review brief называет 9 и 8; идентификатор документа — только дата и «вторая сборка» в `:1,11-16` | Нет уникального revision/hash, поэтому approval может относиться не к той редакции.

2. **С-5 не противоречит CB-66, но предлагает поверхностный split** | CB-66 имеет `status=wont_fix`; ратификация отказа от generated exposure зафиксирована в `PAIN-REGISTRY-codebugs.md:1121-1134`. С-5 явно отказывается повторять generator в `REVISION...md:275-297`, но предлагает вынести `register_tools/register_cli`; сам риск shallow sibling признаётся в `:1175-1179` | Рекомендация согласована с решением владельца, однако может заменить один god-file двумя взаимно зависимыми файлами без уменьшения semantic coupling.

3. **Размер milestone-кандидата фактически описан неверно** | `find src/codebugs/milestones -name '*.py' | wc -l` даёт **8 файлов**, `wc -l` — **2 990 строк**; AST по tool decorators даёт **19 tools**. `src/codebugs/CLAUDE.md:422-430` и части плана продолжают модель «5 modules / 20 tools» | Ошибочная поверхность искажает стоимость поддержки и удаления.

4. **§4 смешивает функции тестов и реально collected cases** | AST `test_*` definitions: sweep 154, merge 70, bench 138, embeddings 43, milestones 185 — всего 590; `pytest --collect-only -s -p no:cacheprovider` дал соответственно 156, 74, 188, 57, 220 — всего **695** | Retirement ratios особенно для parameterized bench-suite занижены; embeddings count в таблице фактически отсутствует.

5. **П-6 использует шумный performance gate** | Критерий `REVISION...md:902-914` требует, чтобы время full suite «не выросло», но не задаёт warm-up, число прогонов, hardware, median или tolerance | Любое изменение можно случайно отклонить или принять из-за системного шума.

6. **П-7/П-11 превращают prose в хрупкий машинный API** | `REVISION...md:916-933,990-1013` предлагает парсить числовые утверждения около anchors, не определяя грамматику, corpus и исключения для дат, exit codes и исторических измерений | Проверка документации станет источником ложных падений и будет поощрять обходные формулировки вместо актуальности.

7. **П-3 обещает универсально проверить несовместимость зависимостей** | `REVISION...md:862-865` требует, чтобы newest-deps check краснел на несовместимой версии «любой зависимости», но не определяет набор mutations или ожидаемые incompatibilities | Resolver обычно просто выберет совместимую версию; критерий невозможно однозначно воспроизвести.

8. **П-5 ограничивает registry идею двумя сущностями и не фиксирует semantics** | `REVISION...md:880-899` обсуждает только findings/requirements; текущая асимметрия подтверждается `src/codebugs/reqs.py:107-119,750-760,1247-1255` и defaults findings в `src/codebugs/findings.py:1634,4621,5466` | Не определены create/update mapping, defaults, coercion, nullable fields и поведение будущей третьей сущности.

## missing_requirements

1. **Нет staged retirement protocol** | В `REVISION...md:706-803,935-947` отсутствуют состояния deprecated → disabled → removed, срок совместимости и критерии перехода | Нужен отдельный процесс для CLI/MCP/API, hooks, schema и данных, а не один owner choice «freeze/delete».

2. **Нет полного FK migration matrix** | П-10 `REVISION...md:968-985` проверяет dangling insert и восстановление PRAGMA, но не existing orphans, `UPDATE/DELETE`, default `NO ACTION`, backup/rollback и atomicity | Включение FK на живой базе может провалить миграцию или изменить операции удаления без диагностируемого пути восстановления.

3. **Нет определения воспроизводимой установки CLI** | pipx metadata `/home/faxik/.local/share/pipx/venvs/codebugs/pipx_metadata.json:70-80` и `direct_url.json:1` показывают установку из локального clone, тогда как `README.md:36-44` предлагает `pipx install codebugs`; установленный Python — 3.14.4 | «Clean machine install» из П-3 не задаёт artifact/source, lock/constraints и допустимость локального checkout.

4. **Не описан drift runtime dependencies** | Сравнение installed metadata с `uv.lock` дало 26 общих packages: 8 одинаковых и **18 различающихся**, включая `mcp 2.0.0↔2.1.1`, `starlette 1.0↔1.6`, `uvicorn 0.42↔0.52.4` | Документ упоминает другой счётчик, но не задаёт поддерживаемую комбинацию и проверку CLI именно против неё.

5. **Нет проверяемой телеметрии retirement** | Источники измерений описаны в `PAIN-REGISTRY-codebugs.md:57-93`, но executable query/report не входит ни в один package | Перед удалением нужен committed read-only audit с периодом, источником данных и отдельным учётом прямых вызовов и hook-driven usage.

6. **Нет самостоятельного пакета для testability bash harness** | Shell harness занимает 4 389 строк по `find tools -type f -name '*.sh' -print0 | xargs -0 wc -l`; `tools/worktree-finish.sh` — 1 251 строка, `tools/_guards.sh` — 800, а `tests/test_worktree_harness.py` — 7 412 строк. План отклоняет проблему только ссылкой на объём тестов в `REVISION...md:1064-1068` | Большой интеграционный test-file не заменяет seams для fault injection, unit testing guards и локализации отказов.

7. **Нет wire-compatibility matrix для ошибок** | П-1/П-2 `REVISION...md:821-851` не перечисляют MCP error codes/messages, CLI exit codes/stderr и допустимые изменения | Рефакторинг error boundaries может незаметно сломать клиентов даже при зелёных внутренних тестах.

8. **Нет критерия глубины для split findings.py** | `findings.py` имеет 6 374 строки, `register_tools` начинается около `:4610`, `register_cli` около `:5362`; П-7 требует только ADR, а не dependency/interface metrics | Формальное сокращение файла не гарантирует более тестируемых и независимых модулей.

## alternatives

1. **Типизированная граница отказов вместо синхронизации tuple-списков** | Ввести базовый `DomainRefusal` с конкретными subclasses; переводить ожидаемые `sqlite3.IntegrityError` в него внутри domain boundary. CLI и MCP ловят один базовый тип, а init/connection errors остаются отдельной категорией.

2. **Использовать существующий MCP middleware для validation normalization** | Расширить `src/codebugs/server.py:418-461`: до `call_next` проверять required/unknown arguments, вокруг него нормализовать SDK validation errors. Добавить wire tests для missing field, wrong type, unknown field и body crash.

3. **Разорвать кольцо composition root, а не переносом helper** | Сохранить обязательные `register_schema/register_tools/register_cli`, но вызвать их из отдельного bootstrap/composition модуля; `db.py` больше не импортирует domains. Критерий — отсутствие `db → domain` edges и SCC между infrastructure/domain, а не уменьшение размера на единицу.

4. **Retirement выполнить поэтапно** | Сначала воспроизводимая usage-проверка; затем deprecation/tombstones; отдельно отключение milestones hooks; экспорт/архивирование таблиц; release boundary; лишь потом удаление source/tests/schema registrations.

5. **Для vocabularies предпочесть behavioral contract tests** | Централизовать Python enum/registry и проверять, что каждое значение принимается, а постороннее отклоняется реальной схемой. Если DDL всё же генерируется, нужен безопасный SQL-literal encoder и явная поправка `CLAUDE.md:400-403`.

6. **Не парсить произвольный prose** | Убрать volatile counts из нормативных разделов либо генерировать отдельную machine-owned таблицу командой аудита. Исторические измерения снабжать датой, revision и сохранённым выводом.

7. **Разделять findings.py по глубоким обязанностям** | Выделять query/mutation/projection/exposure через узкие интерфейсы и измерять dependency edges и isolated tests; не создавать sibling, который импортирует большинство внутренних символов god-module.

8. **Добавить пакет для harness seams** | Вынести чистые проверки из `_guards.sh`/`worktree-finish.sh`, добавить fault-injection тесты внешних команд и разбить 7 412-строчный integration suite по поведению.

## confidence

0.96 — не удалось проверить сеть/PyPI, внешние transcript/tool-call datasets, исторические commit/growth claims, реальные пользовательские SQLite-базы и wall-clock performance; всё остальное проверено по текущему дереву, установленным SDK/pipx metadata и read-only командам.