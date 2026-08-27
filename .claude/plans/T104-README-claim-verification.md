# Т-104 — проверка утверждений README прогоном

Артефакт приёмки юнита Т-104 (направление DIR-1, задача владельца). Главный пункт приёмки от
владельца дословно: **каждое фактическое утверждение README проверено прогоном, а не памятью**, и
предъявляется списком «утверждение — команда — фактический вывод — вердикт».

Все прогоны — во **временных** трекерах, созданных под пробу. Ни одного мутирующего верба на
настоящем трекере проекта. `sqlite3` из командной строки не вызывался ни разу: только инструменты
`codebugs`.

Общий рецепт всех прогонов ниже (`$P` — временный каталог пробы, `$WT` — рабочее дерево ветки):

```
cd "$P"
export PYTHONPATH="$WT/src" CODEBUGS_ROOT="$P"
"$WT/.venv/bin/python" -m codebugs.cli <верб> ...
```

Принадлежность источника подтверждена перед первой пробой:

```
$ python -c "import codebugs; print(codebugs.__file__)"
/home/faxik/w/codebugs/.worktrees/docs-t104-readme-shows-what-this-is/src/codebugs/__init__.py
```

---

## Часть A. Свойства уникальности, которые новый вводный раздел обещает

Это предпосылка §4(а) брифа: каждое обещание вводного текста проверено исполнением. **Если бы хоть
одно не воспроизвелось, README не смягчался бы — это была бы эскалация.**

### A1. Подача одного дефекта дважды не создаёт двух карточек

| | |
|---|---|
| **Утверждение** | «Filing the same finding twice does not create two cards.» |
| **Команда** | `add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42" --new-category`, затем та же команда без флага |
| **Вывод** | `Added: CB-1` → `Bumped: CB-1 (occurrence 2)`; `query --status open` показывает `1 finding(s) total.` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** |

### A2. Закрытая карточка, поданная снова, переоткрывается как регрессия

| | |
|---|---|
| **Утверждение** | «A card that was fixed and comes back is a regression, not a duplicate.» |
| **Команда** | `update CB-1 --status fixed`, затем повторная подача того же наблюдения |
| **Вывод** | `Updated: CB-1 (status=fixed, severity=high)` → `Reopened as regression: CB-1 (occurrence 3)`; в `get CB-1` появилось `meta.regressed = [{"at": ..., "from_status": "fixed"}]`, статус снова `open` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** |

### A3. Решение остаётся решением

| | |
|---|---|
| **Утверждение** | «Re-filing something already dismissed … does not quietly reopen the argument. It files a new card pointing back at the dismissal.» |
| **Команда** | `update CB-1 --status wont_fix`, затем повторная подача того же наблюдения |
| **Вывод** | `Added: CB-2 (recurrence of closed CB-1)`; `get CB-2` даёт `meta.recurrence_of = "CB-1"`; CB-1 остался `wont_fix` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** |

### A4. Серьёзность растёт под наблюдением и не падает

| | |
|---|---|
| **Утверждение** | «Severity only ever escalates under observation … Lowering it back is a deliberate `update`.» |
| **Команда** | подать карточку `high` заново как `critical`, затем заново как `low` |
| **Вывод** | `Bumped: CB-1 (occurrence 4)` → `query --severity critical` показывает CB-1; после подачи как `low`: `Bumped: CB-1 (occurrence 5)`, `query --severity critical` **всё ещё** показывает CB-1 как `critical` |
| **Вердикт** | **ПОДТВЕРЖДЕНО в обе стороны** |

### A5. Место в коде переживает редактирование файла

| | |
|---|---|
| **Утверждение** | «When a report names a line range, the location is anchored at filing time from git. After the file is edited around it, `anchor_resolve` reports where that code went — `moved`, with the new line numbers.» |
| **Подготовка** | во временном git-репозитории файл `src_api.py`, `def beta()` на строке 5; подача `add … --meta '{"lines":"5-7"}'`; затем **вставка 20 строк выше** и коммит (`grep -n "def beta"` → строка 25) |
| **Команда** | `anchor-resolve --finding-id CB-2 --repo "$P" --json` |
| **Вывод** | `"status": "moved"`, `"line": 25`, `"end": 27`, `"channel": "git"`, `"survived": "3/3"` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** — сохранённые номера строк устарели, якорь нет |

### A6. Параллельные агенты не сталкиваются

| | |
|---|---|
| **Утверждение** | «`claims_claim` gives one agent a card and refuses the second, naming who holds it and from which repo … Closing a card releases the claim in the same transaction.» |
| **Команда** | `claim CB-2 --holder agent-A --holder-kind agent --repo "$P"`, затем то же для `agent-B`; затем `update CB-2 --status fixed`; затем `who-holds CB-2` |
| **Вывод** | `claimed CB-2 as agent-A (agent, /tmp/…) touch=1` (код 0) → `REFUSED CB-2: held by agent-A (agent, /tmp/…) since 2026-08-27T11:36:41Z` (**код 3**) → после закрытия `CB-2 not held` |
| **Вердикт** | **ПОДТВЕРЖДЕНО**, включая имя держателя, его вид и репозиторий |

### A7. Инструмент говорит, когда не смог посмотреть

| | |
|---|---|
| **Утверждение** | «an anchor whose card never named a code span — comes back as *undetermined*, with the reason, rather than as a confident wrong answer» |
| **Команда** | `anchor-resolve --finding-id CB-1 --repo "$P" --json` (CB-1 подан без span'а) |
| **Вывод** | `"status": "unknown"`, `"line": null`, `"reason": "no_grammar"`, `"resolved_against": null` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** — вместо выдуманной строки возвращается «не смог, вот почему» |

### A8. Связи между карточками существуют, отчёт о похожести — сухой прогон

| | |
|---|---|
| **Утверждение** | «Cards link to each other, and a similarity report proposes families … as a dry run you inspect before merging anything.» |
| **Команда** | `relations-relate --help`; `similarity-report --help` |
| **Вывод** | `relations-relate` принимает словарь связей `{duplicate_of, split_from, follow_up_of, found_during, distinct_from, related_to}`; у `similarity-report` **нет** флага применения вовсе — только `--threshold/--category/--status/--family-limit/--member-limit/--json` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** — отчёт по построению ничего не сливает |

### A9. `codebugs where` называет привязку и канал

| | |
|---|---|
| **Утверждение** | «`codebugs where` will tell you which tracker a command is actually bound to and which channel decided that» |
| **Команда** | `--tracker-root "$P" where` |
| **Вывод** | `source: --tracker-root` / `root: /tmp/…` / `database: /tmp/…/.codebugs/findings.db` |
| **Вердикт** | **ПОДТВЕРЖДЕНО** |

---

## Часть B. Утверждения, оказавшиеся ШИРЕ кода — и что с ними сделано

### B1. Первый же командный пример README не работает на новом трекере

| | |
|---|---|
| **Утверждение (было)** | строка 183: `codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"` |
| **Команда** | она же, дословно, на трекере сразу после `init` |
| **Вывод** | `category 'n_plus_one' does not exist in this tracker (this tracker has no categories yet); pass new_category=True to mint it deliberately` — **код возврата 1** |
| **Вердикт** | **ЛОЖНО.** Исправлено: в пример добавлен `--new-category`, и рядом объяснено, зачем гейт нужен и что позже флаг не требуется |

Дополнительно измерено, чтобы объяснение не оказалось шире кода:

- `add -c n-plus-one` **без** флага при существующем `n_plus_one` → `Added: CB-2`, код 0 (написание нормализуется, твин не создаётся);
- `add -c totally_new_thing` без флага → `category 'totally_new_thing' does not exist in this tracker (nearest existing: 'n_plus_one'); pass new_category=True…`, код 1;
- `add -c "N Plus One" --new-category` → `categories` показывает **одну** строку `n_plus_one` с тремя находками.

### B2. Обещанная версия зависимости не даст серверу запуститься

| | |
|---|---|
| **Утверждение (было)** | строка 507: «No external runtime dependencies beyond `mcp>=1.0.0` (for the server)» |
| **Команда** | `grep -n -A4 "^dependencies" pyproject.toml` |
| **Вывод** | `"mcp>=2.0.0,<3"` с комментарием: `>=2.0: server.py uses mcp.server.mcpserver.MCPServer, which replaced mcp.server.fastmcp.FastMCP in the 2.0 SDK` |
| **Вердикт** | **ЛОЖНО.** Исправлено на `mcp>=2.0.0,<3` с объяснением, почему нижняя граница не косметическая |

### B3. Счёт инструментов MCP

| | |
|---|---|
| **Утверждение (было)** | строка 24: «66 MCP tools» |
| **Команда** | `python -c "import json; print(len(json.load(open('tests/golden/mcp_schema.json'))))"` |
| **Вывод** | `83` |
| **Вердикт** | **ЛОЖНО.** Счёт **удалён**, а не исправлен: комментарий в самом `server.py:575` уже называл «the 83-tool catalogue», то есть два места репозитория расходились в числе. Вместо числа README отсылает к каталогу, который сервер отдаёт сам |

### B4. Счёт модулей

| | |
|---|---|
| **Утверждение (было)** | строка 24 и заголовок «The nine modules» |
| **Команда** | чтение `choices=` у `--mode` в `src/codebugs/cli.py:288` и `SERVER_NAMES` в `src/codebugs/server.py:557` |
| **Вывод** | у CLI 15 значений (**14** модулей плюс `all`); у сервера 14 (**13** модулей плюс `all`). Расходятся ровно на `usage`, который регистрирует команду CLI и не регистрирует ни одного инструмента MCP (`src/codebugs/usage.py:173`) |
| **Вердикт** | **ЛОЖНО, и хуже: чисел два, и они разные.** Счёт удалён; таблица модулей сделана полной (14 строк) и `usage` помечен как режим только для CLI |

### B5. Пять модулей README не называл вовсе

| | |
|---|---|
| **Утверждение (было)** | таблица «The nine modules» перечисляла девять |
| **Команда** | разность множеств: строки таблицы против списка `--mode` |
| **Вывод** | отсутствовали `loc`, `similarity`, `relations`, `grouping`, `usage` |
| **Вердикт** | **НЕПОЛНО.** Все пять добавлены. Двое из них — `loc` (якоря места) и `similarity` (соседство дедупликации) — несут ровно ту уникальность, ради которой юнит и делался, и получили отдельный раздел с инструментами |

### B6. Число инструментов на модуль — четыре ячейки из десяти ложны

| | |
|---|---|
| **Утверждение (было)** | таблица режимов: findings 8, merge 5, milestones 18, all 66 |
| **Команда** | `server._build_server(mode, conn_factory=…)` для каждого режима, затем `len(server_obj._tool_manager._tools)` |
| **Вывод** | findings **10**, provenance 1, reqs 12, merge **9**, sweep 9, bench 4, blockers 4, milestones **19**, claims 5, similarity 2, grouping 3, relations 3, loc 2; сумма тринадцати = **83** = `all` = длина голдена |
| **Вердикт** | **ЛОЖНО в четырёх ячейках из десяти.** Столбец удалён целиком: это счета, а они в прозе гниют. Верны были provenance, reqs, sweep, bench, blockers, claims |

### B7. Одиннадцать инструментов milestones не имеют команды CLI

| | |
|---|---|
| **Утверждение (было)** | README показывал «типичный цикл» через `pull_next` / `mark_branch_only` / `mark_integrated` / `release_item`, не говоря, что из терминала так нельзя |
| **Команда** | сверка восемнадцати инструментов milestones против ключей `tests/golden/cli_surface.json` |
| **Вывод** | нет верба у `milestone_create`, `milestone_update`, `milestone_add_item`, `milestone_move_item`, `milestone_set_status`, `milestone_defer`, `milestone_close`, `triage_dismiss`, `triage_promote`, `pull_next`, `release_item` — **11 из 18** |
| **Вердикт** | **УМОЛЧАНИЕ.** Названо прямым текстом под таблицей модулей |

### B8. `staleness_check` числился инструментом модуля findings

| | |
|---|---|
| **Утверждение (было)** | таблица «Findings — MCP tools» содержала строку `staleness_check` |
| **Команда** | сборка сервера в режиме `findings` |
| **Вывод** | `add, batch_add, categories, categories_normalize, get, query, recent, stats, summary, update` — `staleness_check` отсутствует; он в модуле `provenance` |
| **Вердикт** | **ЛОЖНО.** Строка перенесена; заодно добавлены три инструмента findings, которых в таблице не было (`get`, `recent`, `categories_normalize`) |

### B9. Пример вывода `codebugs categories` не воспроизводится

| | |
|---|---|
| **Утверждение (было)** | блок без строки-разделителя, числа выровнены вправо |
| **Команда** | воссоздан трекер с теми же числами (15/3/12, 8/2/6, 6/4/2), затем `categories` |
| **Вывод** | есть строка-разделитель `------------------------  -----  ----  -----`, числа выровнены **влево**, ширина колонки категории иная |
| **Вердикт** | **ЛОЖНО по форме, верно по числам.** Блок заменён снятым прогоном |

### B10. «~200 токенов» на вызов `summary`

| | |
|---|---|
| **Утверждение (было)** | строка 365: «returns a structured JSON overview in ~200 tokens» |
| **Команда** | `json.dumps(findings.get_summary(conn))` на двух трекерах |
| **Вывод** | 2 находки → 194 знака (~50 токенов); 29 находок в 29 файлах → 592 знака (~150 токенов) |
| **Вердикт** | **ПРАВДОПОДОБНО, НО НЕПРОВЕРЯЕМО КАК ЧИСЛО** — величина зависит от состава трекера. Число удалено, утверждение оставлено качественным |

### B11. `reqs_get` не был назван

| | |
|---|---|
| **Утверждение (было)** | таблица требований называла 11 инструментов, таблица режимов — 12 |
| **Команда** | сборка сервера в режиме `reqs` |
| **Вывод** | недостающий — `reqs_get` («Fetch a single requirement by ID with full body») |
| **Вердикт** | **НЕПОЛНО.** Строка добавлена |

### B12. Половина модуля merge не была названа

| | |
|---|---|
| **Утверждение (было)** | таблица merge перечисляла 5 инструментов |
| **Команда** | сборка сервера в режиме `merge` |
| **Вывод** | их 9: недоставало `codemerge_claims`, `codemerge_sessions`, `codemerge_status`, `codemerge_abandon` |
| **Вердикт** | **НЕПОЛНО.** Все четыре добавлены |

### B13. «Typical Workflows» и `INSTRUCTIONS` учили разным циклам

| | |
|---|---|
| **Утверждение (было)** | README: `categories` → `add` → автомаршрутизация → `summary` → `query` → `update` |
| **Команда** | чтение `INSTRUCTIONS` (`src/codebugs/server.py:580`) — текста, который получает каждый подключающийся клиент |
| **Вывод** | сервер учит: `add` → **прочитать `dedup_action` и `attention`** → якорь → `update`; плюс клейм при работе рядом с другими; плюс «требования — отдельная сущность без дедупликации» |
| **Вердикт** | **РАСХОЖДЕНИЕ ДВУХ ПОВЕРХНОСТЕЙ.** README приведён к серверу, как и предписал бриф: текст сервера ратифицирован как контракт |

---

## Часть C. Утверждения, проверенные и оставленные без изменений

Проверено выборочно, как и предписано §2а(7) брифа, чтобы не переделывать подтверждённое разведкой.

| Утверждение | Команда | Вывод | Вердикт |
|---|---|---|---|
| «Python 3.11+» | `grep requires-python pyproject.toml` | `requires-python = ">=3.11"` | ВЕРНО |
| «`codebugs where` … prints the resolved root, the database path, and the channel» | `--tracker-root "$P" where` | три строки: `source`, `root`, `database` | ВЕРНО |
| «SQLite (bundled with Python)» | пакет объявляет одну зависимость, `sqlite3` — из стандартной библиотеки | — | ВЕРНО |
| Словарь серьёзностей `critical, high, medium, low` | наблюдалось в `summary` («Open by severity») | четыре ровно эти | ВЕРНО |
| Автомаршрутизация новой находки в `stream/triage` | подтверждено разведкой направления, выборочно не переделывалось | — | ВЕРНО (по разведке) |
| Блок требований целиком, блок обходов, четыре примера этапов выпуска, сообщение об отказе закрытия, предусловия эмбеддингов | подтверждено разведкой направления (§2а(7)) | — | ВЕРНО (по разведке) |

---

## Часть D. Гейт — что он проверяет и, главное, чего НЕ проверяет

`tests/test_readme_surface.py`, три сравнения множеств:

1. множество модулей из таблицы README **равно** списку `--mode` у CLI без `all`;
2. каждый верб из командного примера README **существует** в CLI;
3. каждое имя инструмента из таблиц `| Tool | Purpose |` **существует** в голдене MCP.

**Гейт НЕ проверяет, что README точен.** Он читает три множества имён и сравнивает их с тремя
множествами имён. Он слеп к прозе, к числам, к флагам и их поведению, к точности любого показанного
вывода, к тому, срабатывает ли команда вообще, и к новым вербам и инструментам, которых README не
называет. Это записано теми же словами в докстринге самого гейта.

**Какой из двух списков режимов взят и почему:** список CLI (шире), а не `SERVER_NAMES`. README
описывает флаг, который принимают обе поверхности, а гейт по узкому списку позволил бы молча
выбросить `usage` из README. В таблице README `usage` помечен как режим только для CLI.

### Мутационная проба

Проведена **в отдельном дереве** (копия ветки без `.git` и `.venv`), при `PYTHONDONTWRITEBYTECODE=1`
и нулевом числе каталогов `__pycache__`. База в копии зелена до и после мутаций.

| Мутант | Подтверждение внесения | Результат |
|---|---|---|
| удалена строка модуля `loc` из таблицы README | `grep -c '^| \*\*loc\*\*'` : `1` → `0` | **КРАСНЫЙ** — `assert not ['loc']` |
| добавлен пример `codebugs pull-next --agent-id agent-A` | `grep -c 'codebugs pull-next'` : `1` | **КРАСНЫЙ** — на утверждении о вербах |
| в список `--mode` добавлен `"telemetry"` без строки в README | `grep -c '"telemetry"'` : `1` | **КРАСНЫЙ** — на утверждении о равенстве множеств |
| в таблице инструментов `similarity_check` → `similarity_kheck` | `grep -c 'similarity_kheck'` : `1` | **КРАСНЫЙ** — на утверждении об именах инструментов |

Третий мутант — половина, ради которой гейт и нужен: первые два ловят порчу README, третий ловит
**рост продукта мимо README**.

### «То же состояние, полученное другим способом»

README может разойтись с продуктом **четырнадцатью** найденными способами. Гейт видит **три**.
Честный ответ — меньшинство, и вот он поимённо.

Видит: (1) появился модуль; (2) исчез модуль; (3) верб переименован, а README называет старое имя.
Сюда же попадает (4) переименованный инструмент MCP, названный в таблице `| Tool | Purpose |`.

Не видит: (5) появился верб CLI, которого README не называет; (6) исчез флаг; (7) флаг
переименован; (8) изменился формат вывода команды; (9) появился инструмент MCP; (10) изменилось
число инструментов; (11) изменилась версия зависимости; (12) поведение изменилось при неизменной
поверхности — например, гейт закрытия перестал отказывать; (13) изменилось значение по умолчанию;
(14) инструмент назван только в прозе или в столбце «Headline tools», а не в таблице
`| Tool | Purpose |`.
