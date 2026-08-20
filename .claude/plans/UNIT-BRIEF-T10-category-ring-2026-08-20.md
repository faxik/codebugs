# UNIT-BRIEF Т-10 — category в occurrence-ring + CB-113(b)

Хендофф уровня (3) исполнителю (4). 2026-08-20. Родитель:
`.claude/plans/L3-BRIEF-DIR-2-T10-category-ring-2026-08-20.md`; контракт
`.claude/plans/BT-4-field-freshness-contract.md`, строка `category`, форма (а) —
ратифицирована владельцем. Карта CB-113 (пути (b) и ring-половина (c)); карту НЕ трогать —
она у DIR-2 (CLM-24), статус не менять, клеймов не брать.

## Замысел

Ring несёт улику о категории каждого наблюдения (un-merge возможен), и наблюдение
ИЗВЕСТНОЙ живой карты не гибнет из-за категорийного отказа гейта. Fail-closed гейт
(CB-60) полностью сохраняется для insert-пути: genuinely-new минт по-прежнему требует
`new_category=True`, near-miss на insert-пути по-прежнему отказ с канонической подсказкой.

## Preflight-вердикты менеджера (проверены на main fdf4376, 2026-08-20)

1. `_occurrence_entry` (findings.py:477-509) — ключа `category` нет. Один call site: `_add_one` :721.
2. `_gate_category` бежит в `add_finding` :955-959 ДО `db.txn` (:961) — путь CB-113(b) жив:
   supplied fingerprint в живую карту + near-miss опечатка = `ValueError`, occurrence потерян.
3. `batch_add_findings` гейтует pre-txn в валидационном цикле (:1507-1513) с батч-локальной
   аккумуляцией минтов (`known_categories[category] = category`).
4. **`import_findings` зовёт `_add_one` (:1184-1208) БЕЗ нормализации и БЕЗ гейта** — категория
   peer'а ложится verbatim, минт-стемпа нет. Это поведение обязано сохраниться (CB-51: импорт —
   не наблюдение).
5. Callers `_gate_category`: ровно два (:957, :1511). Callers `_add_one`: три (:962, :1184, :1530).

## Задание (механика выбрана менеджером — предпочтение DIR-2: гейт после определения ветки)

1. **Ring несёт category.** `_occurrence_entry` получает обязательный параметр `category`;
   `_add_one` передаёт категорию наблюдения как она входит в гейт/хеш (на observation-пути —
   нормализованная; импорт — как пришла). Ключ пишется в каждую ring-запись безусловно
   (не `if category:` — `""` легальная категория, и её отсутствие в ринге неотличимо от
   до-фиксовых записей).
2. **Гейт переезжает внутрь `_add_one`, на insert-продолжение** — ПОСЛЕ `return`'ов веток
   bump и reopen, ДО `fid = ...`/`meta_final`. Тем самым отказ вообще не возникает на путях
   известной карты (live-бамп И reopen), а recurrence_of_closed (новая строка) и created
   гейтуются как раньше. Детали:
   - `_add_one` теряет `mint_category: bool`, получает `new_category: bool = False` и
     `gate_category: bool = True`. Внутри, на insert-продолжении, при
     `finding_id is None and gate_category`:
     `mint = _gate_category(_existing_categories(conn), category, new_category=new_category)`;
     `if mint: meta_final["category_minted"] = True`. Предикат явного id сохраняется
     (explicit id — verbatim, без гейта, как сегодня).
   - `add_finding`: `normalize_category` ОСТАЁТСЯ pre-txn (чистая валидация/нормализация,
     вход дериватного хеша — хеш не трогать); блок `_gate_category`/`mint_category` из
     :954-959 уходит; `new_category` передаётся в `_add_one`.
   - `batch_add_findings`: pre-txn гейт (:1506-1513, 1519) уходит; `normalize_category`
     остаётся в валидационном цикле; `new_category` передаётся в `_add_one` per-member.
     Семантика «члены судятся против таблицы ПЛЮС минтов ранних членов батча; стемп только
     у первого» сохраняется автоматически: минт раннего члена — INSERT в открытой txn,
     виден `_existing_categories` на том же соединении. Отказ гейта mid-batch → rollback
     всего батча → как и сегодня, ничего не легло, тот же `ValueError`.
   - `import_findings`: передаёт `gate_category=False` (единственный call site), рядом с
     `annotate=False`/`escalate=False`/`promote_tags=False`, с комментарием того же класса
     (импорт — не наблюдение, категория peer'а не гейтуется — статус-кво preflight-вердикта 4).
   - AST-ратчет на `gate_category=False` — ровно один call site, по образцу
     `tests/test_dedup.py::TestEscalateOptOutRatchet`; плюс тест отсутствия
     `gate_category`/`new_category`-протечки на публичной MCP-поверхности там, где такой
     тест есть у `escalate` (симметрия).
3. **Докстринги** обновить по факту: `add_finding` («Category canon» — гейт решается после
   определения ветки, наблюдение известной живой карты записывается всегда), `batch_add_findings`
   (комментарий «Validate EVERY member before the transaction opens» сузить честно: гейт
   категории теперь внутри txn), `_add_one`, `_occurrence_entry`. ПАРАЛЛЕЛЬНО в том же файле
   идёт Т-11 (докстроки source/reported_at_ref/meta) — не трогай его регионы; конфликт на
   финише решается форвард-мерджем в worktree.
4. **CHANGELOG**: запись о видимом изменении (near-miss/неизвестная категория при supplied
   fingerprint в живую/reopen карту теперь записывает occurrence вместо отказа; ring несёт
   category). Прецедент — запись CB-52.
5. **Wire golden** (`PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`,
   ИЗ worktree) — только если менялись докстринги MCP-обёрток; доменные докстринги golden
   не двигают. Проверь диффом.

## НЕ в скоупе

- CB-113(a) — identity-fork до-гейтовых строк (едет с Т-12 / CB-61).
- Счётчик минта на бамп-пути (хвост (c)) — НЕ делать: решение менеджера, тянет контракт
  стемпа (`category_minted` объявлен insert-only), ring-категории достаточно как улики.
- Кросс-чек категории в `_live_row_by_fingerprint` — не строить.
- Дериватный хеш `auto:v1` — ни байта диффа (category остаётся входом, CB-54/CB-43).
- Файлы DIR-1, `if tag:`-truthiness (CB-29), attention-блок (BT-5).

## Критерии приёмки (TDD — красный тест раньше фикса)

1. Ring-запись нового наблюдения несёт `category` (тест: бамп → `meta.occurrences[-1]["category"]`
   == нормализованная категория наблюдения).
2. **Красный на текущем коде**: карта с supplied fingerprint и категорией X; повторное
   наблюдение с тем же fingerprint и near-miss опечаткой X' без флага → occurrence записан
   (count вырос, ring несёт X'), НЕ `ValueError`. Аналогичный тест на reopen-ветке (fixed
   карта, supplied fingerprint, опечатка → reopened, не потерян).
3. Insert-путь: genuinely-new без флага — отказ с подсказкой; near-miss на insert-пути —
   отказ с канонической орфографией (существующие тесты гейта зелёные, без ослаблений).
4. batch: член с supplied fingerprint в живую карту + опечатка → bumped, не отказ (тест);
   существующие batch-тесты гейта/стемпа зелёные.
5. Импорт не гейтуется (существующее поведение; если теста нет — добавить: импорт строки
   с неизвестной категорией ложится без `new_category` и без стемпа).
6. Fingerprint-контракт без диффа (существующие тесты дедупа/хеша зелёные).
7. Вся сюита зелёная ИЗ worktree: `uv run --extra dev python -m pytest tests/ -q`;
   `uv run --extra dev ruff check src/ tests/` (ruff 0.15.7) чист.

## Протокол

Работа ТОЛЬКО в `/home/faxik/w/codebugs/.worktrees/fix-bt4-category-ring`. На main не писать,
`git checkout <branch> -- <files>` на main = редактирование main. Коммиты по ходу (typed
branch уже есть). Финиш НЕ запускать — это делает менеджер. Возврат: краткий отчёт — что
сделано, какие тесты добавлены (имена), красный-до/зелёный-после факт, результат сюиты и
ruff, замеченные расхождения с этим брифом (пустой список — явно).
