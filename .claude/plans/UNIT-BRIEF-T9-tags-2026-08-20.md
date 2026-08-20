# UNIT-BRIEF Т-9 — посадка ратифицированной строки `tags` контракта BT-4

Формат: полный бриф §12 SPEC-planner-cascade. Автор: менеджер задачи (3), 2026-08-20.
Исходники решения: `.claude/plans/BT-4-field-freshness-contract.md` (строка tags,
ратифицирована владельцем 2026-08-20, форма C-ALT-1),
`.claude/plans/L3-BRIEF-DIR-2-T9-tags-contract-2026-08-20.md`,
`.claude/plans/review-x2-bt4-judge-verdict.md`.

## unit

Т-9: union-merge тегов наблюдения в колонку `tags` на дедуп-бампе (`_bump_row`),
с импорт-опт-аутом `promote_tags=False` и strict-parse хранимых тегов pre-write.
Ветка `fix/bt4-tags-contract` (без id карты — намеренно; population card CB-103
держит DIR-2, юнит её НЕ закрывает).

## intent (замысел)

Наблюдение, принёсшее новый тег, видно тегово-фильтрующим читателям (`query(tag=)`,
`tag_report`), а не только рингу `meta.occurrences`. Импорт — не наблюдение и не
протекает в локальную колонку. Минимальная ратифицированная форма — ничего сверх.

## premises[] (добыты 2026-08-20 менеджером лично, main @ 1468c3b)

1. `_bump_row` (`src/codebugs/findings.py:521-597`): sets-билдер :576-592, каждый
   фрагмент appended вместе со своим параметром; `meta = ?` один, внутри билдера;
   `RETURNING *` + fetchone :594-597. Тегов колонки НЕ трогает — теги наблюдения
   доезжают только до ринга через `entry`.
2. `_occurrence_entry` (`findings.py:470-502`): ring-запись уже несёт `tags` (:496,
   `tags or []`).
3. Три ветки `_add_one` (`findings.py:690-718`): live-бамп :690-699 (`_bump_row`),
   reopen :700-713 (`_bump_row(reopen=True)`), recurrence :714-718 (новая строка —
   теги наблюдения ложатся естественно через INSERT). Ручной
   `update_finding(status=...)` через `_bump_row` НЕ проходит.
4. Импортные опт-ауты (`findings.py:1131-1148`): `import_findings` → `_add_one` с
   `annotate=False`, `escalate=False` — единственный call site `escalate=False`
   (пин: `tests/test_dedup.py::TestEscalateOptOutRatchet`, AST).
5. `update_finding(tags=)` — полный replace (`findings.py:1578-1580`), один
   `tags = ?` в билдере `updates`. НЕ меняется этим юнитом.
6. Tag-фильтр `query_findings` (`findings.py:1865-1867`) — `json_each` по колонке;
   `if tag:` truthiness — территория CB-29, НЕ трогать.
7. Коррапт-классификация: `tests/test_dedup.py:582`
   `TestStoredCorruptionClassification` — сейчас пинует malformed-stored-TAGS как
   post-commit (`PostCommitCorruptionError`, `test_malformed_stored_tags_is_postcommit_and_lands`,
   occurrence_count становится 2). Strict-parse pre-write ОСОЗНАННО меняет этот
   класс; тест переписывается с объяснением в докстринге. Два ambient-теста
   (:610, :625) ожидают raw `json.JSONDecodeError` — останутся зелёными, но их
   смысл (точка raise) сдвигается на pre-write; докстринги поправить.
   `PostCommitCorruptionError` (`findings.py:791`) НЕ удаляется — `_finalize_add`
   по-прежнему может встретить malformed tags на путях, где бамп их не парсил бы…
   ВНИМАНИЕ: при strict-parse в `_bump_row` malformed tags на бамп-пути перестаёт
   достигать `_finalize_add`; если после правки класс становится недостижим для
   tags, это констатировать в докстринге исключения, НЕ удаляя его (meta-путь
   update_finding и защитная роль остаются; минимальная форма).
8. `parse_tags` (`findings.py:1666-1679`) — tolerant parse для ЧТЕНИЯ/дисплея;
   не смешивать со strict-parse записи, не менять.
9. `TestBumpSqlComposition` (`tests/test_dedup.py:954`) — шаблонный SQL через
   `RecordingConnection`; расширяется: `tags = ?` ровно один.
10. Т-8 идёт параллельно в том же `findings.py` (докстринг импорта ~:1049,
    SCHEMA-блок); на момент добычи НЕ приземлён (main 1468c3b). Финиши
    сериализует flock; при отказе финиша — форвард-мердж и повтор.

## prescription (hypothesis — ратифицированная форма C-ALT-1)

1. `_bump_row` получает параметр `promote_tags: bool = True` и теги наблюдения
   (через уже передаваемый `entry["tags"]` или отдельный аргумент — исполнителю
   решить по коду, НЕ меняя семантику; entry уже несёт список). При
   `promote_tags=True`:
   - strict-parse хранимых тегов залоченной строки: `json.loads(row["tags"])`
     PRE-write; malformed → `json.JSONDecodeError` до любого write (симметрия с
     meta :561); non-list результат парса — обращаться консервативно (displaced,
     как ring-защита :566-567, НЕ TypeError);
   - union: merged = хранимые + наблюдённые, точное строковое равенство (НИКАКОГО
     casefold), первый-встреченный порядок (хранимые раньше наблюдённых),
     дедупликация;
   - запись ТОЛЬКО при изменении множества — допустимо и без условия, но ровно
     один `tags = ?` ВНУТРИ билдера `sets` со своим параметром (CB-16, парность
     фрагмент+параметр);
   - сериализация контейнера ОДИН раз (`json.dumps` единожды, тот же строковый
     объект уходит в параметр — CB-82);
   - `meta = ?` остаётся один; `RETURNING *` + fetch остаётся.
   При `promote_tags=False`: колонка tags не пишется вовсе; strict-parse при этом
   НЕ выполняется (импортный live-hit не обязан падать на чужом коррапте, бамп
   должен лечь — сегодняшнее поведение сохраняется, ринг несёт теги наблюдения).
2. `_add_one` получает `promote_tags: bool = True`, пробрасывает в оба вызова
   `_bump_row` (live и reopen). Recurrence и insert — без изменений (теги
   наблюдения естественно в INSERT).
3. `import_findings` передаёт `promote_tags=False` — РОВНО один call site, рядом с
   `escalate=False` (:1147), с комментарием того же класса («импорт — не
   наблюдение, чужие теги не продвигаются в локальную колонку»).
4. Публичная поверхность: `add_finding` / `batch_add_findings` НЕ получают
   `promote_tags` в сигнатуру. `escalate` НЕ перегружается.
5. МЕХАНИЗМ СНЯТИЯ НЕ строится: `update(tags=)` остаётся полным replace; снятый
   руками тег может вернуться следующим наблюдением. Суб-решение (кэп / tombstones
   / `finding_tags`) ОТКРЫТО владельцем — именованной строкой в CHANGELOG и в
   CLAUDE.md-тексте.
6. Смежность НЕ трогать: `if tag:` (:1865, CB-29), `parse_tags`, tag-фильтры.

## acceptance (тесты, обязанные падать при мутации)

- НОВЫЙ: карта, ре-наблюдённая с новым тегом, находится `query(tag=)` по нему —
  КРАСНЫЙ на откате union-правки (мутационная проба приёмки).
- НОВЫЙ: reopen-бамп несёт union (регрессия — наблюдение, `fixed-in-1.2` доезжает).
- НОВЫЙ: импортный live-hit НЕ приносит чужих тегов в колонку (но ринг несёт).
- НОВЫЙ: порядок первый-встреченный, точное равенство (`Tag` != `tag` — оба живут),
  дедупликация.
- НОВЫЙ: сериализация один раз — мутирующий/`__iter__`-переопределяющий контейнер
  по паттерну CB-82-теста (см. tests/test_bench.py — один dumps, тот же текст).
- НОВЫЙ: AST-ратчет `promote_tags=False` — ровно один call site (образец
  `TestEscalateOptOutRatchet`, AST не grep).
- НОВЫЙ: surface-absence — `promote_tags` отсутствует в сигнатурах
  `add_finding`/`batch_add_findings`.
- НОВЫЙ/структурный: ручной `update_finding` пути union не приобрёл (структурный
  довод: не вызывает `_bump_row`; пин — тест, что `update_finding(status=...)` не
  меняет tags-колонку).
- ИЗМЕНЁННЫЙ ОСОЗНАННО: `test_malformed_stored_tags_is_postcommit_and_lands` →
  malformed stored tags на promote-бамп-пути теперь pre-write
  `json.JSONDecodeError`, rollback, occurrence_count == 1; докстринг объясняет
  сдвиг классификации (BT-4 строка tags, strict-parse ратифицирован).
- РАСШИРЕННЫЙ: `TestBumpSqlComposition` — `sql.count("tags = ?") == 1` + binding
  proof (теги легли теговые, meta — метовые).
- Существующая сюита зелёная: `uv run --extra dev python -m pytest tests/ -q` ИЗ
  worktree; `uv run --extra dev ruff check src/ tests/` (ruff 0.15.7).
- CHANGELOG-запись (прецедент CB-52): union на бампе; reopen тоже union; импорт не
  протекает; «снятие тегов пока полный replace — суб-решение (кэп / tombstones /
  finding_tags) открыто владельцем»; strict-parse сдвиг коррапт-классификации.
- CLAUDE.md: текст контракта tags в секцию дедупа (пункт (9) после (8) CB-52),
  включая открытое суб-решение. Перед касанием проверить in-flight (Т-8).
- Wire golden: только если описания MCP-инструментов изменились —
  `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`
  ИЗ worktree.

## preflight (вердикты 5 проверок §4, advisory; добыто 2026-08-20)

1. Цитированные file:line существуют и говорят утверждаемое — ДА, с дрейфом:
   sets-билдер :576-592 (бриф DIR-2 называл :576-591); импортные опт-ауты
   :1131-1148 (бриф: :1121-1147). Символы совпадают, суть верна.
2. Предпосылки против схемы: колонка `tags` TEXT `NOT NULL DEFAULT '[]'`, без
   CHECK/json_valid — union-запись `json.dumps(list)` совместима. ДА.
3. Названный файл — место фикса: `src/codebugs/findings.py` + `tests/test_dedup.py`. ДА.
4. Пересечение с in-flight: Т-7 (`tools/`, worktree fix-cb-116-merge-subject) — не
   пересекается; Т-8 (тот же findings.py, не приземлён на 1468c3b) — сериализация
   flock'ом финишей + форвард-мердж, по брифу DIR-2. ПРИНЯТО.
5. Бриф свежий (2026-08-20), повторный прогон не требуется.
