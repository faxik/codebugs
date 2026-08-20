# UNIT-BRIEF Т-11 — декларации `source` / `reported_at_ref` / `meta` контракта BT-4 (docs-only)

Юнит уровня (4) каскада. Менеджер задачи — уровень (3). Ветка `fix/bt4-declarations`,
worktree `/home/faxik/w/codebugs/.worktrees/fix-bt4-declarations`. Работать ТОЛЬКО в worktree.
Контракт: `.claude/plans/BT-4-field-freshness-contract.md`, строки `source` (§3),
`reported_at_ref` (§4), `meta` (§5) — ратифицированы владельцем 2026-08-20 как
«заморозка, объявленная словами»: **НОЛЬ поведенческих правок**.

## Жёсткая граница

Дифф `src/` может состоять ТОЛЬКО из докстрингов/строк описаний. Любая правка
исполняемого кода (условия, SQL, сигнатуры, значения) = выход из скоупа = СТОП и доклад
менеджеру, ничего не коммитить сверх уже сделанного. Файлы юнита:
`src/codebugs/findings.py` (докстринги), `CLAUDE.md`, `CHANGELOG.md`,
`tests/golden/mcp_schema.json` (регенерация), новые пин-тесты в `tests/`.
Никаких других файлов. Карт у юнита нет; CB-103 держит DIR-2 — НЕ трогать её статус.

## Содержание (все места сверены менеджером по текущему main, fdf4376)

### 1. `source` — frozen by design, first-reporter. Три читателя:

- **`query` MCP-докстринг**, Args-строка `source:` (`findings.py:2295`, сейчас
  «Filter by source (claude, ruff, human, etc.)»): объявить — фильтр сравнивает с
  ПЕРВЫМ репортером; колонка заморожена при первом репорте, источники позднейших
  наблюдений живут только в occurrence-ring (`meta.occurrences[*].source`), и
  ring-source импортированного наблюдения может быть источником peer-трекера.
- **`group_by="source"`** — строки group_by в `query`-докстринге (`:2302`) и
  `stats`-докстринге (`:2375`): краткая пометка, что группы по source считают
  первых репортеров (одна короткая вставка, не абзац).
- **MCP `add` проза**, Args-строка `source:` (`:2138`, сейчас «Who created this
  finding (default: claude)»): → «First reporter of this defect (default: claude).
  Frozen at first report by design: re-observations keep the original; newest
  sources live in the occurrence ring — and an imported observation's ring source
  can be a peer tracker's.» (формулировка примерная, смысл обязателен).
- Опционально симметрично: одно предложение в докстринге домена `add_finding`
  (`:913`) — source/meta/reported_at_ref observation-frozen, ring несёт улику.
  Дёшево одним предложением на все три поля, не тремя абзацами.

### 2. `reported_at_ref` — observation-frozen, manually mutable BY DESIGN:

- **`add` MCP**, Args `reported_at_ref:` (`:2142`): дополнить — колонка
  observation-frozen (бамп её не обновляет; per-occurrence refs — улика в ринге),
  но manually mutable через `update(reported_at_ref=)` — релиз тегируется после
  файлинга.
- **`update` MCP**, Args `reported_at_ref:` (сейчас «Update version/tag label»):
  объявить это САНКЦИОНИРОВАННОЙ ручной мутацией observation-frozen колонки.
  Симметрично — одно предложение в докстринге домена `update_finding` (`:1561`,
  рядом с существующей фразой про immutable `reported_at_commit` — НЕ путать поля:
  commit immutable, ref mutable by design).
- **`query` MCP**, Args `ref:` (`:2300`, «Filter by reported_at_ref (exact
  match)»): объявить семантику — точное совпадение с «первым наблюдённым ЛИБО
  вручную назначенным релизным ref» (= текущее поведение); ring-читателя нет и
  не строится до появления потребителя latest-observation-семантики.

### 3. `meta` — top-level авторское состояние, observation-frozen:

- **`add` MCP**, Args `meta:` (`:2140`): top-level meta — авторское состояние
  строки, observation-frozen: meta повторного наблюдения ложится только как
  per-occurrence улика в `meta.occurrences[*].meta`; продвижение конкретных
  ключей в строку — будущий allowlist по замеренному спросу, не общий merge.
- **`query` MCP**, Args `meta_key:`/`meta_value:` (`:2297-2298`): объявить — оба
  читают авторское top-level состояние строки (json_extract по колонке), не ring.

### 4. CLAUDE.md + CHANGELOG

- CLAUDE.md, дедуп-буллет CB-43 (строка-буллет «Findings have an identity
  function…», сейчас заканчивается пунктом (9) от Т-9 про union тегов): дописать
  компактный пункт **(10)** — по ОДНОМУ предложению на поле: `source` = первый
  репортер, frozen by design (закрывает CB-21-ячейку source как ОБЪЯВЛЕННУЮ
  immutability; в будущем parity-тесте — во frozenset IMMUTABLE с причиной);
  `reported_at_ref` = observation-frozen, manually mutable by design,
  `query(ref=)` матчит первый-наблюдённый-либо-назначенный ref; top-level `meta`
  = авторское состояние, наблюдённая meta — улика в ринге, продвижение ключей —
  будущий allowlist по спросу. Пометить: BT-4, ратифицировано 2026-08-20,
  поведение не менялось.
- CHANGELOG.md `[Unreleased]`: короткая запись (документационный контракт трёх
  полей объявлен словами; поведение не менялось). Секция — по вкусу структуры
  файла (`### Documentation` или аналог, НЕ под Fixed).

### 5. Wire golden — ОБЯЗАТЕЛЬНО

MCP-описания меняются ⇒ из worktree:
`PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`.
Дифф golden обязан состоять ТОЛЬКО из описаний (description-строк). Проверить
диффом и сказать в отчёте.

### 6. Пин-тесты (образец — Т-8: `tests/test_findings.py::` пин
`import_findings.__doc__`, ~:1738-1755: regex-извлечение из докстринга +
сверка с исполняемой константой/фактом)

TDD: докстринг-пины пишутся ПЕРВЫМИ и красные до правки прозы. Поведенческие
пины замороженного поведения будут зелёными на обеих сторонах — это легально
ТОЛЬКО с явной пометкой в докстринге теста («pins deliberately preserved
behaviour», правило CLAUDE.md Testing-раздела). Кандидаты (сначала проверь,
что не дублируешь существующие тесты — ring-тесты и пин mutable-ref из CB-53/
provenance уже есть, греп по `reported_at_ref` в tests/):

- `query(ref=)` «exact match»: строка с ref="v1.0" НЕ находится по ref="v1"
  (точность, не префикс) и находится по полному значению — контрольный пин
  текущего поведения, которое проза объявляет.
- `meta` observation-frozen: бамп с `meta={"k":"v"}` — top-level meta строки
  БЕЗ "k" (⇒ `query(meta_key="k")` не видит карту), а ring-запись несёт его.
- `source` frozen: бамп с другим source — колонка держит первого репортера,
  ring несёт нового.
- Докстринг-пин по образцу Т-8 там, где проза утверждает исполняемый факт
  (например: Args-строка `ref:` содержит «exact» — и SQL-условие в
  `query_findings` — `reported_at_ref = ?`). Не городить хрупких мегарегексов;
  1-2 докстринг-пина достаточно. Где пинить структурно нечем — написать в
  отчёте явно, с причиной.

## Процедура

1. Свериться: `git log --oneline -3` в worktree (база — main fdf4376 или новее).
2. Пин-тесты (красные докстринг-пины) → правка прозы → зелёные.
3. Полная сюита ИЗ worktree: `uv run --extra dev python -m pytest tests/ -q`.
4. Линт: `uv run --extra dev ruff check src/ tests/` (ruff 0.15.7).
5. Регенерация golden (см. §5), проверка состава диффа.
6. Коммиты по-домашнему (`docs(bt4-t11): …` / `test(bt4-t11): …`); finish НЕ
   запускать — интеграцию делает менеджер. Если main уехал (Т-10/CB-116
   финишировали параллельно) — НЕ форвард-мерджить самому, доложить.
7. Самопроверка диффа: `git diff main...HEAD -- src/` — только
   докстринги/описания. Приложить вывод `git diff main...HEAD --stat`.

## Возврат (тройка)

1. Результат: что объявлено, где, SHA коммитов.
2. Доказательства: состав диффа src/ (только строки), состав диффа golden
   (только описания), тесты (число passed), линт, красно-зелёный цикл пинов.
3. Эскалации: слот ОБЯЗАТЕЛЕН; пустой — явно «эскалаций нет».

## Preflight-вердикты (§4, advisory — сверка по символам, менеджер, 2026-08-20)

Все 10 якорей брифа сверены по main fdf4376 до диспетчеризации и повторно
приёмкой — текст совпал дословно: `findings.py` :2295 (query source:), :2302
(query group_by:), :2375 (stats group_by:), :2138 (add source:), :2140 (add
meta:), :2142 (add reported_at_ref:), :913 (add_finding docstring), :1561
(update_finding docstring), :2300 (query ref:), :2297 (query meta_key:).
In-flight на момент старта: fix-cb-116-merge-subject (CLAUDE.md Workflow-секция
+ tools/ — непересекающиеся регионы); Т-10 шёл параллельно и приземлился на
main (0671881) ДО интеграции этого юнита — финиш форвард-мерджит его.

## Факт приёмки (3)←(4), §13 — менеджер Т-11, 2026-08-20

Исполнение: свежий субагент (П-В), только worktree; коммиты b45fd1c (docs:
декларации + golden + CLAUDE.md (10) + CHANGELOG) и e9e405a (tests: пины).

1. Замысел покрыт: три читателя `source` (query(source=) :2317, group_by в
   query :2331 и stats :2409, проза MCP add :2145 с peer-ring-оговоркой);
   `reported_at_ref` на add :2153 / update :2263 (санкционированная ручная
   мутация) / query(ref=) :2328 («первый наблюдённый ЛИБО назначенный», exact,
   never prefix) + домен :1569 рядом с immutable `reported_at_commit`;
   `meta` на add :2149 и обоих фильтрах :2323; домен add_finding :947.
   CLAUDE.md — пункт (10) после (9); CHANGELOG — `### Documentation`,
   «no behaviour changed» явно. append_note в CB-21 — за менеджером после
   мерджа (write в трекер, сознательно не в юните). PASS.
2. Мутационная проба руками (менеджер, поверх пробы приёмщика): detached
   worktree на e9e405a; baseline `TestBt4FreshnessDeclarations` — 2 passed;
   `git checkout fdf4376 -- src/codebugs/findings.py` (grep 'First reporter'
   = 0 — мутация применена) — 2 FAILED; восстановление — зелёные. PASS.
3. Предпосылки: 10 якорей preflight сверены дословно (выше). PASS.
4. Возврат воркера — тройка с явным слотом эскалаций («эскалаций, требующих
   решения, нет»; наблюдение: main уехал fdf4376→d07c559, дельта plans-only).
   PASS.
5. Артефакт уровня: факт приёмки — этот раздел, закоммичен на ветке; строка в
   леджере DIR-2 — за направлением по его регламенту. PASS (в объёме юнита).
6. Интеграция (адаптирован): дифф src/ докстринг-only доказан структурно (AST
   modulo docstrings identical, fdf4376 vs HEAD); golden-дифф — ровно 4
   description-строки (add, query, stats, update); сюита из worktree 1712
   passed; ruff 0.15.7 чист. Финиш `tools/worktree-finish.sh` — сразу после
   этого коммита, с форвард-мерджем Т-10 и повторной проверкой состава
   golden-диффа; мердж-SHA — в тройке менеджера.
7. Карт у юнита НЕТ; CB-103 держит DIR-2 (CLM-18, жив, projected
   in_progress) — юнит статусов карт не трогал (дифф не содержит .codebugs/),
   CB-103 НЕ закрывается этим юнитом. PASS, явным вердиктом.
