# UNIT-BRIEF Т-5 — CB-53: staleness читает ре-наблюдение (полный формат, §12 SPEC)

Дата: 2026-08-19. Уровень (3) → (4). Родитель: L3-BRIEF-DIR-2-T5-cb53-staleness-reobservation-2026-08-19.md.
Карта: CB-53 (in_progress, клейм перейдёт ветке `fix/cb-53-staleness-reobservation`).
Решение ратифицировано владельцем (meta CB-53 / CB-63): карта decision-free, implementation-only.

## unit

Читательская половина CB-53: `check_findings` / `staleness_check` в
`src/codebugs/provenance.py` консультируют новейшее наблюдение (occurrence-ring) вместо
только первозаписи. Write-путь `findings.py` — ZERO diff.

## intent (замысел)

`reported_at_commit` строки ЗАМОРОЖЕН — first-report provenance (ратифицировано, вариант
(b) CB-63). Дефект, ре-наблюдённый минуты назад на HEAD, не должен репортиться протухшим
по первозаписи — это регрессия точности staleness, внесённая дедупом CB-43 (до него каждое
ре-наблюдение было новой строкой со штампом HEAD). Чиним ЧТЕНИЕ: staleness сверяется с
новейшим наблюдением, где оно есть; где его нет (старый корпус) — поведение прежнее.

## premises[] (все передобыты мной 2026-08-19, provenance ниже)

- P1. `provenance.py:616-624`: `check_findings` ключует кэш и вердикт на
  `(f["file"], f.get("reported_at_commit"))` и передаёт в `file_status` только
  первозапись. Ре-наблюдение невидимо. [прочитано мной сегодня]
- P2. `findings.py:360-392` (`_occurrence_entry`): каждая запись ring несёт
  `at`, `reported_at_commit` (может быть None), `reported_at_ref`, severity, file, …
  Ring живёт в `meta["occurrences"]` (`findings.py:457-465`), append в хронологическом
  порядке; переполнение усекается keep-first+keep-last (`_OCC_KEEP_FIRST`/`_OCC_KEEP_LAST`),
  так что НОВЕЙШИЕ записи всегда в хвосте списка. [прочитано мной сегодня]
- P3. `findings.py:1635+` (`query_findings`) делает `SELECT *` и конвертит через
  `db.row_to_dict` (`db.py:623-634`), который ПАРСИТ `meta` в dict. Значит строки,
  которые `check_findings` уже получает (`provenance.py:578-600`), несут parsed
  `meta["occurrences"]` и `last_seen_at` — ring читается БЕЗ каких-либо правок
  findings.py, через существующий публичный интерфейс. [прочитано мной сегодня]
- P4. NULL-предпосылка: строки до дедупа несут `last_seen_at = NULL` и meta без
  `occurrences` — живой пример сам CB-53 (`last_seen_at: null` в трекере, meta без ring).
  Фолбэк на первозапись ОБЯЗАТЕЛЕН, иначе staleness ломается на старом корпусе.
  [проверено по живому трекеру сегодня]
- P5. `findings.py:1342-1343`: `reported_at_commit` intentionally excluded из
  `update_finding` — immutable after insert. (Meta карты цитирует :1274 — код уехал,
  содержание в силе; дрейф цитаты отмечен, не блокирует.)
- P6. `provenance.py:257+` (`file_status`) принимает КОММИТ (`reported_at_commit=`),
  не таймстемп. `last_seen_at` — whole-second TEXT-таймстемп без коммита.

## Выбор формы (делегирован (3), сделан на preflight)

**Форма: новейший валидный `reported_at_commit` из occurrence-ring, фолбэк — строковый
`reported_at_commit` (первозапись).** `last_seen_at`-форма ОТКЛОНЕНА: `file_status`
git-сравнивает против коммита (P6), таймстемп в него не подаётся; маппинг
таймстемп→коммит (`git rev-list --before`) — новый subprocess на карту и ложь на
переписанной истории. Ring уже несёт per-occurrence коммит (P2) и достижим без правки
findings.py (P3) — это и обходит пересечение с in-flight Т-3 (CB-60, findings.py).

Точная семантика «effective commit» для карты f:
1. `ring = f["meta"]["occurrences"]`, если meta — dict и occurrences — list; иначе ring
   пуст (defensive: meta/ring бывают рукописными — см. `_bump_row`'s defensive re-typing).
2. Скан ring С ХВОСТА (новейшие в хвосте, P2): первая запись, которая dict и несёт
   `reported_at_commit` непустой строкой, — даёт effective commit. Записи-мусор (не-dict,
   None-коммит, не-строка, пустая строка) пропускаются, не роняют проверку.
3. Ничего не нашлось → effective commit = `f.get("reported_at_commit")` (текущее
   поведение, P4).
Обоснование шага 2: наблюдение без коммита (auto-capture не сработал) не должно прятать
более раннее наблюдение С коммитом — любое ring-наблюдение новее первозаписи по построению.

## prescription (ГИПОТЕЗА; расхождение с реальностью = стоп и доклад)

Только `src/codebugs/provenance.py` + `tests/test_provenance.py`:
1. Хелпер в provenance.py (например `_effective_commit(f) -> str | None`) с семантикой
   выше, с докстрингом, называющим ратификацию (CB-53/CB-63: столбец заморожен, читатель
   консультирует ring).
2. В `check_findings` (:616-624): `cache_key = (f["file"], effective)`; `file_status(...,
   reported_at_commit=effective, ...)`. Кэш-ключ ОБЯЗАН использовать effective — иначе две
   карты с одним (file, первозапись) но разными ring-коммитами делят один вердикт.
3. В результат карты добавить поле `checked_commit` (= effective; равен
   `reported_at_commit`, когда ring пуст) — вердикт, не называющий коммит, против
   которого проверял, недиагностируем. Существующее поле `reported_at_commit` НЕ трогать
   (совместимость, first-report provenance).
4. Докстринг MCP-инструмента `staleness_check` (:758+) дополнить: сверка идёт с новейшим
   наблюдением, поле `checked_commit`. Докстринг = description в wire golden →
   регенерировать: `PYTHONPATH=src uv run python tests/dump_schema.py >
   tests/golden/mcp_schema.json` (строго ИЗ worktree, PYTHONPATH=src обязателен).
5. TDD: тесты ПЕРЕД правкой, обязаны падать на нетронутом читателе.

ГРАНИЦЫ: findings.py — zero diff (`git diff` по нему пуст; `_bump_row` не трогать).
НЕ трогать файлы DIR-1: merge.py, milestones/, blockers.py, bench.py, cli.py, server.py,
tools/. Сигнатуры MCP-параметров не меняются.

## acceptance (тест обязан падать при мутации-откате)

- A1 (основной, мутационный): карта с первозаписью на старом коммите, файл с тех пор
  изменён → без правки `file_status=modified`; после ре-наблюдения (dedup-bump через
  публичный `add_finding` с тем же fingerprint-входом и `reported_at_commit=HEAD`) →
  `check_findings` даёт `current` и `checked_commit=HEAD`. Тест ОБЯЗАН падать при откате
  читательской правки (проба выполняется руками при приёмке).
- A2 (legacy/NULL): карта без ring и без last_seen_at → вердикт байт-в-байт как раньше
  (фолбэк на первозапись); существующие provenance-тесты зелёные.
- A3 (мусор в ring): новейшая запись с `reported_at_commit=None` → берётся более ранняя
  запись с валидным коммитом; ring целиком без коммитов → фолбэк на первозапись;
  не-dict запись не роняет проверку.
- A4 (кэш-ключ): две карты, один file и одна первозапись, разные ring-коммиты → разные
  вердикты (дискриминация кэша).
- A5 (write-путь): `git diff main -- src/codebugs/findings.py` пуст.
- Полная сюита: `uv run --extra dev python -m pytest tests/ -q` ИЗ worktree; `ruff check`
  (ruff 0.15.7). Golden регенерирован, если description изменился (шаг 4).

## preflight (вердикты 5 проверок §4, advisory; добыл менеджер Т-5, 2026-08-19)

1. Цитаты: provenance.py:617-632 — ДЕРЖИТСЯ (прочитано; кэш-ключ :617, вызов :619-624);
   findings.py:387 (ring несёт reported_at_commit per-occurrence) — ДЕРЖИТСЯ
   (:360-392); findings.py:1274 (immutability) — ДРЕЙФ цитаты, реальное место :1342-1343,
   содержание в силе. Не блокирует.
2. Схема/конфиг: колонка `last_seen_at` в SCHEMA (findings.py:46) + миграция (:178-179);
   ring в meta["occurrences"]; NULL-предпосылка подтверждена живым CB-53. Python 3.11+ —
   ничего сверх stdlib не требуется. ДЕРЖИТСЯ.
3. Файл фикса: provenance.py — да, там и check_findings, и file_status, и MCP-обёртка.
   ДЕРЖИТСЯ.
4. Пересечение с in-flight: Т-3 (CB-60) держит findings.py (клейм CLM-12 жив, ветки ещё
   нет) — наш юнит findings.py НЕ трогает (P3 делает аксессор ненужным). DIR-1-файлы не
   затрагиваются. Живой worktree fix-cb-106 — чужой файл (codemerge). ЧИСТО при
   соблюдении границ.
5. Бриф свежий (тот же день) — повторный прогон не требуется.

## Возврат (4)→(3)

Тройка: результат; доказательства (дифф-статистика, имена новых тестов, вывод полной
сюиты и ruff, подтверждение A5, регенерация golden — да/нет и почему); эскалации —
слот обязателен, пустой — явно. Коммиты на ветке `fix/cb-53-staleness-reobservation`,
worktree `.worktrees/fix-cb-53-staleness-reobservation`. Мердж НЕ делать — интеграцию
(worktree-finish) выполняет менеджер после приёмки.

## Приёмка (3)←(4) — неизменяемый факт

Юнит Т-5 принят менеджером (3) 2026-08-19 на коммите 2df5c10 (ветка
fix/cb-53-staleness-reobservation). §13: (1) дифф прочитан против замысла — форма
и границы соблюдены, findings.py zero diff; (2) мутационная проба выполнена руками:
откат читательской правки → 7/7 новых тестов падают (A1 падает на вердикте
modified≠current); (3) предпосылки P1–P6 перепроверены против приземлённого кода;
(4) слот эскалаций исполнителя — явно пуст; (5) этот факт; (6) worktree-finish —
см. мердж-коммит на main; (7) CB-53 → fixed после мерджа (автоснятие branch-клейма).
