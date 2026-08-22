# UNIT Т-22 — CB-128: `query(commit=)` видит occurrence-ring

Бриф юнита по SPEC-planner-cascade §12 (полный формат). Уровень (3) → (4).
Ветка `fix/cb-128-query-commit-ring`, база `3e91dad` (main). Дата: 2026-08-22.
Входящий хендофф: `.claude/plans/L3-BRIEF-DIR-2-T22-query-commit-ring-2026-08-22.md`.
Образцы: `UNIT-T16-CB-123-recent-closed.md`; сестринский читатель `provenance._effective_commit` (CB-53).

## unit

CB-128 — единственная карта юнита. Клейм взят ХАРНЕСОМ из имени ветки (Э-4):
`codebugs who-holds CB-128` → `held by fix/cb-128-query-commit-ring (branch, /home/faxik/w/codebugs)`.

## intent (замысел, не задание)

Вопрос читателя «что наблюдалось на коммите X» должен отвечаться по ВСЕМ наблюдениям
карты, а не только по первому. Колонка `reported_at_commit` заморожена на первом репорте
намеренно (CB-53 (b), ратифицировано через CB-63); ре-наблюдения лежат в ринге
`meta.occurrences[*].reported_at_commit` (CB-43). CB-53 научил этому одного читателя
(`check_findings` через `_effective_commit`); `query(commit=)` остался «вторым
ring-blind читателем» (x2-ревью BT-4, judge п.4). **Колонка остаётся замороженной —
юнит строго read-side.** Семантика — «ЛЮБОЕ наблюдение», не «новейшее»: `query(commit=X)`
спрашивает «что видели на X», для этого все наблюдения равноправны; `_effective_commit`
ищет новейшее, потому что отвечает на другой вопрос (насколько карта устарела).
Расхождение ОСОЗНАННОЕ — записывается в докстринге обоих мест.

## premises[] (fresh, добыто 2026-08-22 чтением дерева на `3e91dad`)

Грепаемые цитаты (якорись ими, не номерами):

- **Пишущая сторона ринга** — `_occurrence_entry` в `findings.py` пишет ключ БЕЗУСЛОВНО:
  `"reported_at_commit": reported_at_commit,` (значение `None` → JSON `null`, когда
  авто-захват недоступен). `_bump_row` собирает ринг как список dict'ов:
  `prior = meta.get("occurrences")` / `ring = list(prior) if isinstance(prior, list) else []`
  / `ring.append(entry)` / `meta["occurrences"] = ring`. Форма ринга = список объектов
  с ключом `reported_at_commit` — совпадает с тем, что читает `_effective_commit`.
- **Текущий фильтр** в `query_findings`:
  ```
  if commit:
      if not re.fullmatch(r"[0-9a-fA-F]+", commit):
          raise ValueError(f"commit filter must be hex, got: {commit!r}")
      conditions.append("reported_at_commit LIKE ? || '%'")
      params.append(commit.lower())
  ```
  Предикат `if commit:` — truthiness (ловушка 2 брифа: известное место CB-25/CB-29 для
  free-text-фильтров — НЕ чинить, см. эскалации).
- **Несущий порядок параметров** — комментарий `# PARAMETER ORDER IS LOAD-BEARING.` в
  `query_findings`; `rank_case_sql`-параметры идут после WHERE-параметров, до `LIMIT`.
  Условия собираются в `conditions`/`params` парно — новая ветвь добавляет ОДНО условие
  и ДВА параметра одним и тем же значением в момент добавления.
- **Существующая meta-ветвь**: `conditions.append("json_extract(meta, ?) = ?")` /
  `json_extract(meta, ?) IS NOT NULL` — без guard'а на malformed meta.
- **MCP-докстринг `query`**: `commit: Filter by reported_at_commit (prefix match, hex validated)`.
- **CLI `query`** (парсер `sub.add_parser("query", help="Search findings")`, хендлер
  `_cmd_query`): флага `--commit` НЕТ, `query_findings(` вызывается без `commit=`.
  Это дыра CB-6 — в этом юните не закрывается (эскалация).
- **Сестринский читатель** `provenance._effective_commit`: докстринг начинается
  `The commit staleness is checked against: newest ring observation, else first report.`;
  скип не-list ринга, не-dict элемента, не-строкового/пустого коммита.
- **Golden**: `tests/golden/mcp_schema.json`; регенерация ИЗ worktree
  `PYTHONPATH=src uv run --extra dev python tests/dump_schema.py > tests/golden/mcp_schema.json`.
- **Существующие тесты**: `tests/test_findings.py` уже содержит
  `findings.query_findings(conn, commit="a1b2c3d4e5")` (префикс по колонке) и
  `commit="not-hex!"` → `ValueError`.

## preflight (вердикты 5 проверок §4)

1. **Форма ринга из пишущей стороны** — **ДЕРЖИТСЯ.** Список dict'ов, ключ
   `reported_at_commit` пишется всегда (`null` при отсутствии). Читатель и писатель
   согласны; тест фикстуры обязан это УТВЕРЖДАТЬ (ловушка 3).
2. **Malformed meta — ИЗМЕРЕНО (SQLite 3.47.1, через `uv run python`):**
   - существующая `meta_key`-ветвь (`json_extract(meta, ?) IS NOT NULL`) на строке с
     `meta='{not json'` → `OperationalError: malformed JSON` на ВСЮ выборку;
   - `EXISTS(SELECT 1 FROM json_each(json_extract(meta,'$.occurrences')) …)` без guard'а —
     то же падение;
   - `json_valid(meta) AND EXISTS(...)` — фактически короткозамкнул (вернул только живую
     строку), но короткое замыкание `AND` в SQLite НЕ документировано → не опираться;
   - `CASE WHEN json_valid(meta) THEN EXISTS(...) END` — документированное ленивое
     вычисление ветвей CASE; malformed-строка отдаёт NULL → не матчится; измерено: живая
     строка найдена, malformed пропущена, ошибки нет;
   - **НЕПРЕДВИДЕННОЕ брифом:** элемент ринга, не являющийся объектом
     (`{"occurrences":["abc777"]}`), роняет `json_extract(json_each.value, '$.reported_at_commit')`
     тем же `malformed JSON` — `json_valid(meta)` от этого НЕ защищает (meta валиден).
     Лечится `json_each.type = 'object'` внутри EXISTS (измерено на 9 фикстурах: malformed
     meta, NULL meta, `{}`, скаляр-ринг, dict-ринг, строковый элемент, верхний регистр,
     двойное совпадение — ошибок нет). `json_type(meta,'$.occurrences')` на malformed meta
     САМ падает, поэтому проверка формы массива допустима только ВНУТРИ THEN после `json_valid`.
   - `json_valid(NULL)` → NULL → CASE даёт NULL → строка без meta не матчится по рингу (и не падает).
   - Регистр: `LIKE` регистронезависим для ASCII — ринг с `ABCDEF` найден фильтром `abcdef`.
     `lower()` в SQL не нужен.
   - Двойное совпадение (колонка И ринг): через `OR` + `EXISTS` строка вернулась ОДИН раз.
   **Вывод:** новая ветвь делается СТРОГО ЛУЧШЕ существующей `meta_key`-ветви (не падает
   на malformed meta и на мусорном ринге), а не «не хуже».
3. **`NULL` в ринге** — **ДЕРЖИТСЯ.** `json_extract(value,'$.reported_at_commit') LIKE …`
   на `null` → NULL → не матчится; измерено на фикстуре `ok` со вторым элементом `null`.
4. **Регистр** — **ДЕРЖИТСЯ** (см. п.2). Колонка хранит как пришло, фильтр делает
   `commit.lower()` + `LIKE`; ринг — тот же `LIKE`, тот же параметр.
5. **Описания** — **ДЕРЖИТСЯ с поправкой:** MCP-докстринг `commit:` есть и правится;
   CLI `--commit` НЕ существует → правки CLI help нет, дыра CB-6 в эскалацию.
   Т-21 (`feature/cb-127-expose-grouping`, `35f2285`): замерено `git diff --stat
   main...feature/cb-127-expose-grouping` — `findings.py` НЕ тронут, golden +201 строк
   (новые инструменты grouping). Конфликт возможен только в golden → форвард-мердж + реген.

## Форма SQL (предписание исполнителю)

Заменить единственное условие `reported_at_commit LIKE ? || '%'` на дизъюнкцию:

```
(reported_at_commit LIKE ? || '%'
 OR CASE WHEN json_valid(meta) THEN
      json_type(meta, '$.occurrences') = 'array'
      AND EXISTS (SELECT 1 FROM json_each(meta, '$.occurrences')
                  WHERE json_each.type = 'object'
                    AND json_extract(json_each.value, '$.reported_at_commit') LIKE ? || '%')
    END)
```
с `params.extend([commit.lower(), commit.lower()])` СРАЗУ при добавлении условия.
Где разошлось с брифом: бриф предлагал `json_each(json_extract(meta,'$.occurrences'))`
и один guard `json_valid`; измерение потребовало второй guard `json_each.type='object'`
(строковый элемент ринга) и проверку `json_type = 'array'` (зеркало `isinstance(ring, list)`
у `_effective_commit` — dict-ринг иначе матчится по значениям). Многострочность
фрагмента — допустима, `conditions` склеиваются через `' AND '` с обёрткой в скобки.

## acceptance (критерий, ратифицированный владельцем, по смыслу)

Тесты — новый файл `tests/test_query_commit_ring.py`; фикстуры через РЕАЛЬНЫЙ путь
`add_finding` дважды с разными `reported_at_commit` (дедуп-бамп), с УТВЕРЖДЕНИЕМ фикстуры
(`dedup_action == "bumped"`, ринг несёт оба коммита — прочитать через `get_finding`).

1. Запрос по коммиту РЕ-наблюдения находит карту, первозапись которой на другом коммите.
2. По коммиту ПЕРВОЙ записи карта по-прежнему находится; строка без ринга (одиночный add)
   ведёт себя как раньше.
3. Префиксный матч на обеих ветвях (короткий префикс коммита ре-наблюдения).
4. `commit="zz"` → `ValueError` (hex-валидация не ослаблена).
5. Карта, совпавшая и колонкой, и рингом, возвращается РОВНО один раз (`total == 1`,
   `len(findings) == 1`).
6. `LIMIT`/`OFFSET`/`group_by` не ломаются под `commit=` + другой фильтр (например
   `severity=`) — пин несущего порядка параметров.
7. Ринг с `reported_at_commit: null` (второй add с `reported_at_commit=None` — ПРОВЕРИТЬ,
   что `add_finding` в домене не авто-захватывает HEAD; авто-захват живёт в MCP-обёртке)
   не матчится ни по какому коммиту, кроме колонки.
8. Malformed meta на ЧУЖОЙ строке не роняет `query(commit=)` (строго лучше `meta_key`);
   строковый элемент ринга на чужой строке — тоже. Эти строки сажаются прямым UPDATE
   `meta` (это тест устойчивости к мусору, а не фикстура ринга — так и написать в докстринге).
9. Верхний регистр в ринге / в фильтре — находится.
10. Докстринг MCP `query` `commit:` и докстринг `_effective_commit` (одна перекрёстная
    строка) называют расхождение; golden регенерирован ИЗ worktree — дифф только описание
    `query`.
11. CHANGELOG `[Unreleased]` → `### Fixed` (или `Changed`): абзац CB-128.

## Эскалации исполнителя (§13 п.4 — слот обязателен; заполняет (4) ДОСЛОВНО)

Слот НЕ пуст. Дословно из отчёта исполнителя (4):

1. **«NULL meta» из измерений брифа (`json_valid(NULL) → NULL`) — недостижимое состояние в
   этой схеме.** Колонка `findings.meta` объявлена `NOT NULL`: прямой `UPDATE ... SET meta = NULL`
   падает с `IntegrityError`. Я удалил эту пробу из `test_8c`; SQL-форма по-прежнему корректна
   для NULL, но тест её подтвердить не может. Бриф в этом пункте описывает случай, которого в
   таблице не бывает.
2. **Пункт 8 сужен до контракта фильтра.** Первая версия `test_8a` дополнительно просила
   *вернуть* саму мусорную строку по её колонке — это падает в `db.row_to_dict`
   (`JSONDecodeError` при сериализации malformed meta), что есть ранее существующее поведение
   сериализатора, а не фильтра. Тест теперь считает совпадение колонки через `group_by` (COUNT
   без сериализации строк) и объясняет почему. Бриф говорит «не роняет `query(commit=)`» —
   фильтр не роняет; возврат строки с мусорным meta — отдельный, не этого юнита, вопрос.
3. **Форма SQL** применена как предписано (не как в исходном L3-брифе) — расхождение уже
   зафиксировано в самом юнит-брифе, я его не расширял.
4. CLI `--commit` отсутствует — не закрыто, названо в CHANGELOG, как и предписано.

Разбор (3): п.1 — принято, бриф неверен в букве (NULL meta), замысел (guard не падает) держится
и покрыт `json_valid` по измерению; п.2 — принято, это CB-16-класс post-commit сериализации,
вне юнита; п.3–4 — ожидаемо. Ни одно не блокирует.

Эскалации уровня (3) вверх — см. возврат (2): (а) CLI `query` без `--commit` (дыра CB-6);
(б) предикат `if commit:` truthiness-овый (CB-25/CB-29, не чинить здесь); (в) предложение
правки CLAUDE.md — одна фраза в пункте (10) BT-4 про `query(commit=)` как ring-aware читателя.

## Приёмка (§13, семь пунктов — заполняет (3))

Принято 2026-08-22 на `f02b62b` (ветка `fix/cb-128-query-commit-ring`, база `3e91dad`).

1. **Критерий приёмки — ПРИНЯТО.** `tests/test_query_commit_ring.py`, 11 тестов, все пункты
   1–9 брифа; ключевой `test_1_re_observation_commit_finds_card_first_reported_elsewhere`.
   Фикстуры — реальный `add_finding` дважды, `_ring_card` УТВЕРЖДАЕТ `dedup_action == "bumped"`,
   замороженную колонку и содержимое ринга. Докстринг MCP `query` несёт
   `Matches the first-report column OR any occurrence in the ring` и
   `staleness_check uses the NEWEST ring entry instead: a different question`;
   `_effective_commit` несёт перекрёстную строку `Sister reader: findings.query_findings(commit=)`.
2. **Мутационная проба — ПРИНЯТО, воспроизведена МНОЙ во ВРЕМЕННОМ worktree** на `6d42137`
   (рабочее дерево не мутировалось; временный worktree удалён):
   - М1 — ветвь ринга снята, условие возвращено к `reported_at_commit LIKE ? || '%'` →
     **7 красных** (`test_1`, `test_3`, `test_6`, `test_9`, `test_8a/8b/8c`);
   - М2 — EXISTS заменён на `LEFT JOIN json_each(findings.meta, '$.occurrences')` без DISTINCT →
     ПЕРВЫЙ прогон: красный только `test_8a` (malformed), а тест «не дважды» `test_5` — ЗЕЛЁНЫЙ:
     ринг из одного элемента не отличает EXISTS от JOIN. Это вакуозный тест — дефект приёмки
     исполнителя, исправлен мной (`f02b62b`): ринг несёт ДВА совпадающих элемента, JOIN даёт
     `total == 3`. Повтор М2 → **2 красных** (`test_5` на `assert res["total"] == 1`, `test_8a`).
3. **Предпосылки preflight против приземлённого кода — ПРИНЯТО.** Грепом в дереве:
   `"reported_at_commit": reported_at_commit,` (`_occurrence_entry`), `meta["occurrences"] = ring`,
   `# PARAMETER ORDER IS LOAD-BEARING.`, `params.extend([commit.lower(), commit.lower()])`,
   `json_each.type = 'object'`, `json_type(meta, '$.occurrences') = 'array'`.
4. **Слот эскалаций — ЗАПОЛНЕН ЯВНО**, см. выше. Не пуст.
5. **Артефакт уровня обновлён** — этот раздел. Строка в документ DIR-2 — рукой (2) (К-1).
6. **Post-merge-контроль — заместитель `tools/worktree-finish.sh`** (форвард-мердж main,
   ruff, полная сюита из объединённого дерева, `--no-ff` под локом). До него из worktree:
   `1903 passed`, `All checks passed!` (ruff 0.15.7).
7. **Карта юнита ровно одна** — CB-128; попутных карт не заводилось. `fixed` — после мерджа.

**Свойство диффа:** `findings.py` +34/−2 — единственные удалённые строки — старые две строки
условия `commit`; `provenance.py` +3 (докстринг); golden `1 insertion, 1 deletion` — только
`description` инструмента `query`; `tools/`, `db.py`, `server.py`, `cli.py`, CLAUDE.md не тронуты.
Параллельный Т-21 (`feature/cb-127-expose-grouping`) не трогает `findings.py`; пересечение
возможно только в golden и снимается регенерацией из объединённого дерева.
