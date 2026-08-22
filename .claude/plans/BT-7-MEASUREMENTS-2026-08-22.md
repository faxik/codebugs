# BT-7 — перемер несущих чисел (канон К-5), ДО написания v3

**Кто мерил:** держатель направления DIR-2, уровень (2) каскада, сессия 4.
**Когда:** 2026-08-22, между 14:55 и 15:40 UTC, на машине владельца.
**Чем:** экспортом боевых трекеров через собственный CLI пакета + четырьмя скриптами,
приведёнными в приложении ДОСЛОВНО. Никакого прямого SQL; ни один пробник не касается
боевого трекера на запись — каждый строит собственный трекер во временном каталоге.

**Зачем этот файл существует.** Канон К-5 (реестр болей, приёмка сессии 3): «для BT
обязательный перемер несущих чисел держателем ОТДЕЛЬНЫМ шагом до первого x2, с записью
кто и когда мерил». Повод: **два дизайн-дока DIR-2 подряд провалили x2 ровно в секциях,
претендовавших на „измерено“.** v1 отложила один греп «на перепроверку при ревью» — он
опроверг две из трёх рекомендаций. v2 была собрана за один проход и построила ★-выбор
хранения на популяции, которой нет. Поэтому здесь сначала числа, и только потом текст.

**Отношение к v3:** ни одно число ниже не выбрано под желаемый вывод — раздел 9 честно
перечисляет, что подтвердилось, что сдвинулось и что ОПРОВЕРГЛО мои же предыдущие
формулировки, включая случаи, где перемер сделал дизайн сложнее, а не проще.

---

## 1. Корпуса и снимки — точные команды

```
uv run codebugs export-csv \
    <SP>/bt7-measure/codebugs-live-2026-08-22.csv
#   -> Exported 129 findings

uv run codebugs --tracker-root /home/faxik/w/autosorter export-csv \
    <SP>/bt7-measure/autosorter-live-2026-08-22.csv
#   -> Exported 3300 findings
```

Оба — **живые трекеры на 2026-08-22**, а не снимок. v2 мерила autosorter по кэшу
`~/.cache/codebugs-identity/corpus.csv` от 2026-08-17 (3176 строк); я его НЕ использую:
снимок недельной давности был отдельной претензией обоих атакующих, и живой трекер
доступен. Новейшая строка в codebugs — `2026-08-22T10:42:02Z`, в autosorter —
`2026-08-22T12:27:29Z`.

**Расхождение счёта строк, названное вслух:** Opus-атака на v2 писала «130 rows, live,
today», Codex — «129 and 3296». Мой замер даёт **129**, то есть совпадает с Codex;
цифра 130 у Opus, судя по всему, включала строку заголовка CSV. Записываю, потому что
именно такие расхождения на единицу оба раза оказывались симптомом.

---

## 2. Блок A — популяция ре-наблюдений (число, обрушившее v2)

Скрипт: `measure.py`, блок M2.

| корпус | строк | `occurrence_count > 1` | строк с непустым `meta.occurrences` | заведено ≥ 2026-08-16 (посадка CB-43) | из них с >1 |
|---|---|---|---|---|---|
| codebugs (живой) | 129 | **0** | **0** | 87 | **0** |
| autosorter (живой) | 3300 | **0** | **0** | 153 | **0** |

Распределение `occurrence_count` — ровно `{1: N}` в обоих корпусах. Ни одной строки с
кольцом наблюдений ни в одном из двух трекеров, за шесть дней после посадки функции
идентичности, при 87 + 153 = **240 карт, заведённых уже через неё**.

**Отдельный пробник, решающий, что означает этот ноль** (`probe_premises2.py`, P-i):
механизм НЕ сломан. Две подачи одного отпечатка в чистый трекер дают:

```
add #1 -> id=CB-1 was_new=True  action=created
add #2 -> id=CB-1 was_new=False action=bumped
row.occurrence_count = 2
ring length = 1; ring entry keys = ['at','category','description','file',
                'reported_at_commit','reported_at_ref','severity','source','tags']
severity after re-observation = high   (эскалация low->high отработала)
```

**Значит ноль — факт об ИСПОЛЬЗОВАНИИ, а не о коде.** Оба трекера наполняются
руками/агентами, которые каждый раз формулируют новый дефект, а не переподают старый.
Это ровно то различие, которого не было в v2: v2 обосновывала хранение популяцией,
а не механизмом, и популяция оказалась пустой.

Побочно измерено, для оценки цены кольца: **одна запись кольца сегодня = 250 байт JSON**
(без всякого якоря); `_OCC_KEEP_FIRST=10`, `_OCC_KEEP_LAST=10`, `_OCC_DESC_CAP=2000`,
и переполнение сохраняет `ring[:10] + ring[-10:]` — **первые десять записей не
вытесняются никогда**.

---

## 3. Блок B — перепись локационных ключей и грамматика

### 3.1 Перепись ключей (`measure.py`, M3/M4)

| ключ | codebugs (129) | autosorter (3300) |
|---|---|---|
| `lines` | 44 — все `str` | 559 — `str` 351, `list` 206, `dict` 2 |
| `line` | 0 | 98 — `int` 95, `str` 3 |
| `sites` | 18 — `str` 17, `list` 1 | 36 — `list` 24, `str` 10, `dict` 2 |
| `site` | 0 | 16 — все `str` |
| `function` | 0 | 28 — все `str` |
| `location` | 0 | 1 |
| `anchor` | 0 | 18 — **значения = id карт**, имя занято под другой концепт |
| `loc` | **0** | **0** |

Длины `list`-значений `lines` (autosorter, 206 списков, **206 из 206 — целиком int**):
`{1:40, 2:76, 3:42, 4:25, 5:8, 6:2, 7:3, 8:3, 9:1, 10:3, 13:1, 16:1, 20:1}`.
Эта перепись **воспроизводит перепись v2 построчно** (v2: «len1 40, len2 76, len3 42,
len4 25, len5 8, len6–20 15»; 2+3+3+1+3+1+1+1 = 15 ✓) — то есть та часть §1а была верна.

**Ключ `loc` свободен в обоих корпусах (0/129, 0/3300)**, но окрестность плотная:
`handler_loc`, `loc_blocker`, `loc_src`, `loc_tests`, `fix_locus`, `root_cause_loc`,
`proposed_loc`, `repo_loc`, `est_size_loc`, `ceiling_file_loc`, `name_gen_loc`,
`baseline_files_loc`, `real_class_location`, `actual_location`, `fix_location`,
`error_location`, `hang_location`, `location`. Свободным `loc` останется только за счёт
самого резервирования.

### 3.2 Ветви грамматики (`measure3.py`, G1 — регексы опубликованы в приложении)

| ключ | корпус | B1 bare int | B2 list[int] | B3 `path:N` токен(ы) | B4 голая спека `a-b`/`N,M` | B5 dict спек | B6 проза |
|---|---|---|---|---|---|---|---|
| `lines` | codebugs 44 | — | — | 32 | 10 | — | 2 |
| `lines` | autosorter 559 | — | 206 | 52 | 292 | 2 | 7 |
| `line` | autosorter 98 | 95 | — | — | 3 | — | — |
| `sites` | codebugs 18 | — | — | 6 | — | — | 12 |
| `sites` | autosorter 36 | — | — | 32 (из них 20 — **список** `path:N` строк, 2 — dict) | — | — | 4 |
| `site` | autosorter 16 | — | — | 15 | — | — | 1 |
| `location` | autosorter 1 | — | — | 1 | — | — | — |
| `function` | autosorter 28 | — | — | — | — | — | 28 |

### 3.3 Сколько ФАЙЛОВ называет строка — самый решающий замер (`measure3.py`, G4)

Вопрос задан один раз на строку, объединением по всем локационным ключам: сколько
РАЗНЫХ имён файлов вообще упомянуто в локационных значениях этой карты?

| корпус | строк с любым локационным ключом | 0 имён | 1 имя | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|---|
| codebugs | 61 | **23** | 23 | 12 | 2 | 1 | — |
| autosorter | 709 | **609** | 47 | 23 | 17 | 6 | 7 |

**609 из 709 строк autosorter (86%) не называют в локационном значении НИ ОДНОГО имени
файла** — значение несёт только номера строк, а файл берётся из колонки `file`. В
codebugs так у 23 из 61. Господствующий диалект — **колонка `file` плюс голая спека
строк**, а ветвь `file:N` — меньшинство. v2 построила §4 вокруг `file:N`-совпадения,
то есть вокруг меньшинства популяции.

И когда имя ВСЁ-ТАКИ названо ровно одно, оно колонке не противоречит (G5):
codebugs **согласие 22 / расхождение 1**; autosorter **41 / 6**. То есть токен — не
конкурент колонке, а подтверждение.

### 3.4 Базнейм-гейт: выход ветви, которую v2 приняла, но не измерила (`measure3.py`, G2)

| ключ | корпус | строк с токеном | ровно одно имя | **ГЕЙТ ПРОЙДЕН** | несовпадение | много имён (отказ) | точное совпадение полного пути |
|---|---|---|---|---|---|---|---|
| `lines` | codebugs | 32 | 20 | **19** | 1 | 12 | 2 |
| `lines` | autosorter | 52 | 22 | **18** | 4 | 30 | 0 |
| `sites` | codebugs | 6 | 3 | **3** | 0 | 3 | 0 |
| `sites` | autosorter | 32 | 9 | **7** | 2 | 23 | 2 |
| `site` | autosorter | 15 | 15 | **15** | 0 | **0** | 7 |
| `location` | autosorter | 1 | 1 | **1** | 0 | 0 | 1 |

Числа `19/32` (codebugs) и `18/52` (autosorter) воспроизводят замер Opus-атаки
(19/32 и 17/40) с точностью до роста корпуса и ширины моего токен-регекса. Ветвь жива;
v2 публиковала счёт для написания, которое ОТВЕРГЛА (2/31, 0/38 точных совпадений
полного пути), и ни одного — для принятого.

**`site` в единственном числе — 15 из 15 проходят гейт и 0 многофайловых.** `sites` во
множественном — 23 из 32 многофайловые. Имя ключа честно говорит о кардинальности,
и это разные ветви, а не «та же обработка».

### 3.5 Сколько МЕСТ называет значение (`measure3.py`, G3)

| ключ | корпус | n | много-МЕСТ | много-ФАЙЛОВ |
|---|---|---|---|---|
| `lines` | codebugs | 44 | 24 (54%) | 12 (27%) |
| `lines` | autosorter | 559 | 268 (47%) | **30 (5%)** |
| `sites` | codebugs | 18 | 11 (61%) | 3 (16%) |
| `sites` | autosorter | 36 | 34 (94%) | 23 (63%) |
| `site` | autosorter | 16 | **0** | 0 |
| `line` | autosorter | 98 | **0** | 0 |

**Много-МЕСТ — норма (47–54% у `lines`), много-ФАЙЛОВ — редкость (5% у autosorter).**
Это разные утверждения, и v2 их смешивала: её «мульти-МЕСТО 30/32» я **не воспроизвёл ни
при одном из трёх определений** (`measure2.py` считает три: >1 токен `file:N`; >1 разных
имён файлов; >1 атомарных ссылок на строку — для codebugs выходит 23, 12 и 24 из 44).
Воспроизводится только «мульти-ФАЙЛ 12» — оно верно. Число 30/32 несущее (им
обосновывался `sites_dropped` и «один якорь отвечает полностью на 2 карты из 32») и
**оно не подтвердилось**.

Строк, несущих ДВА и более локационных ключа сразу: **codebugs 1** (`lines`+`sites`),
**autosorter 28** (`lines`+`function` 26, `line`+`function` 1, `lines`+`line`+`function` 1).
Совпадает со счётом Codex (28). Уточнение к Codex: комбинации `lines`+`sites` в
autosorter НЕТ — единственная такая строка живёт в codebugs.

Форма `[0, 0]`: **0 строк в обоих корпусах** — то есть она по-прежнему только в исходнике
живого писателя, как и говорила v2. Наибольшее целое в локационном значении:
codebugs 2211, autosorter **7412** (v2 писала «реальный максимум в корпусе — 5708»).

---

## 4. Блок C — популяция колонки `file` (`measure.py`, M9)

| класс | codebugs | autosorter |
|---|---|---|
| обычный читаемый файл | 125 | 2907 |
| несуществующий путь | 3 | 197 |
| глоб / каталожная нотация | 0 | 167 |
| каталог | 1 | 22 |
| проза / не-путь | 0 | 7 |
| **итого** | **129** | **3300** |

Суммы сходятся точно. v2 писала «~2787/3176» и «~381», причём 2787 + 381 = 3168 ≠ 3176 —
восемь строк не учитывались (находка W-3 атаки). Здесь недостачи нет.
**Заякорить можно 2907 из 3300 (88%) в autosorter и 125 из 129 (97%) в codebugs**;
остальным честный `unknown(<причина>)`.

---

## 5. Блок D — стоимости (`probe_cost.py`), измерены здесь, а не унаследованы

v2 несла «23.7 ms/add» и «~2.4 s на батч в 100 членов» как ЧУЖИЕ замеры, и на них
опирался весь довод в пользу двухфазного захвата. Пул кандидатов similarity — newest-500,
поэтому цена растёт с корпусом, и мерить на пустом трекере бессмысленно:

| глубина пула | `add_finding` | `batch_add_findings(100)` |
|---|---|---|
| ~0 строк | 1.44 ms/add | 125 ms (1.25 ms/член) |
| ~200 | 3.52 ms/add | 303 ms (3.03) |
| ~600 | 8.24 ms/add | 830 ms (8.30) |
| ~1200 | 16.23 ms/add | 1337 ms (13.37) |

Порядок величины чужого замера подтверждается на глубоком пуле; **форма — рост с пулом —
подтверждается прямо.**

**А вот число, которого у v2 не было вовсе — цена самого захвата:**

```
read + decode + \r\n-нормализация + splitlines + вырезание сегмента + два sha256
  28 реальных исходников (p50 19 KB):  5.4 ms всего  =  0.192 ms/файл
  самый большой файл пакета (findings.py, 184 KB), 200 повторов: 0.671 ms/захват
```

**Захват стоит 0.19–0.67 мс при том, что лок УЖЕ удерживается на 8–16 мс чисто
процессорной работы резолвера similarity.** То есть захват под локом — это +2…+8% к
цене, которую дизайн уже принял. Честный предел замера назван в §10: кэш тёплый.

---

## 6. Блок E — калибровка чисел, которые v2 назвала, но не дала (`probe_calibrate2.py`)

### 6.1 Профиль файлов двух реальных репозиториев (даёт `max_bytes_read`)

59 161 файл (`.py .js .ts .md .sh .sql .yml .yaml .toml`, без `.git/.venv/node_modules/
__pycache__/.worktrees/.codebugs`):

| | codebugs (247) | autosorter (58 914) | оба |
|---|---|---|---|
| байт p50 | 15 024 | 4 945 | — |
| байт p99 | 163 471 | 129 529 | **129 738** |
| байт max | 188 333 | **5 051 718** | 5 051 718 |
| строк p99 | 2 548 | 3 369 | **3 369** |
| строк max | 3 975 | **135 926** | 135 926 |

Файлов больше 1 MiB: **10 из 59 161 (0.02%)**; больше 256 KiB: 182 (0.31%).

### 6.2 Уникальность нормализованного сегмента ВНУТРИ своего файла

Это и есть калибровка, которой требовал урок `MIN_TEXT_LEN=40` из similarity. Корпус —
52 260 python-файлов обоих репозиториев, позиции сэмплированы через одну из двадцати
(n ≈ 1.01 млн на каждый пролёт).

| длина якоря | пусто/пробелы | НЕ уникален в своём файле | остаётся неуникальным ПОСЛЕ 3 строк контекста |
|---|---|---|---|
| 1 строка | 14.3% | **25.8%** | **3.82%** |
| 2 строки | 1.8% | 18.5% | 3.64% |
| 3 строки | 0.0% | 12.2% | 3.14% |
| 5 строк | 0.0% | 6.6% | 2.46% |

Неуникальность ОДНОЙ нормализованной строки по длине её тела:

| длина тела | 0–9 | 10–19 | 20–29 | 30–39 | 40–49 | 50–59 | 60+ |
|---|---|---|---|---|---|---|---|
| не уникальна | **69.2%** | 35.6% | 28.0% | 22.4% | 20.0% | 17.1% | **14.4%** |

**Три вывода, и они не тривиальны:**

1. **Контекст делает в 6.8 раза больше работы, чем длина якоря.** Одна строка плюс
   3 строки контекста: 25.8% → 3.82%. Растянуть якорь с 1 до 5 строк БЕЗ контекста:
   25.8% → 6.6%. То есть `context_hash` обязателен не «для аккуратности», а потому
   что он и есть основной различитель.
2. **Длина сама по себе никогда не делает якорь уникальным**: даже при 60+ символах
   14.4% строк повторяются внутри своего же файла. Значит порог `MIN_ANCHOR_CHARS`
   не может быть механизмом уникальности — он только отсекает мусор. Колено кривой на
   **20–30 символах** (28.0% → 22.4% → 20.0%), а ниже 10 символов якорь — шум (69.2%).
3. **Остаточная неоднозначность ~2.5–3.8% никуда не девается ни при каком пролёте.**
   Значит `ambiguous` — штатный исход с измеренным полом, а не краевой случай.

### 6.3 Спека хеша существующего отпечатка — чтобы якорь от неё не разошёлся

```python
canonical = json.dumps(
    [category, file, _normalize_for_fingerprint(description, meta)],
    ensure_ascii=False,
)
return _AUTO_V1_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
```

То есть: массив (не склейка), `ensure_ascii=False`, разделители по умолчанию,
`utf-8`, sha256, усечение до 32 hex.

---

## 7. Блок F — кодовые предпосылки, проверенные ИСПОЛНЕНИЕМ, а не чтением

`probe_premises.py` / `probe_premises2.py`. Каждая решает пункт v3.

| # | предпосылка | вердикт | что именно вернул код |
|---|---|---|---|
| P-a | кольцо наблюдений недостижимо ни одним пишущим API | **ПОДТВЕРЖДЕНА** | `update_finding(meta_update={"occurrences": …})` → `ValueError: meta keys ['occurrences'] are reserved for the identity machinery`. `_RESERVED_META_KEYS = ['occurrences','occurrences_dropped','recurrence_of','regressed']`; `resolver_updatable_meta_keys() = ['similar_to']` |
| P-h | регистрация резолвера ЕСТЬ резервирование ключа | **ПОДТВЕРЖДЕНА** | `add(meta={"similar_to": …})` → тот же `ValueError` |
| P-e | `updatable_keys` открывает update БЕЗ всякой валидации | **ПОДТВЕРЖДЕНА** | `update_finding(meta_update={"similar_to": "NOT-A-LIST-AT-ALL"})` принят, строка сохранена дословно |
| P-g | отпечаток дериваируется ДО резолверов (структурная гарантия Р1) | **ПОДТВЕРЖДЕНА** | в исходнике `_add_one`: `_derive_fingerprint` на смещении 3064, `run_pre_add_resolvers` на 8031 |
| P-g2 | «ловушка уплощения» из П8 | **УТОЧНЕНА (v2 была шире правды)** | см. ниже |
| P-d | импорт срезает зарезервированное только на ВЕРХНЕМ уровне | **ПОДТВЕРЖДЕНА** | `_import_meta` фильтрует `raw.items()`, то есть кольцо теряется целиком, а не выборочно |
| P-c | парсер переименований непригоден для `-U0 --find-renames` | **ПОДТВЕРЖДЕНА** | `_parse_rename_records(<вывод -U0>)` → `None`; тот же парсер на `--name-status -z` → `[('old.py','new.py')]` |
| P-b | `describe_root()` — корень ТРЕКЕРА, не worktree | **ПОДТВЕРЖДЕНА** | в linked worktree: `describe_root().root='/tmp/…/main'` (source=`discovery`), `provenance._repo_root(wt)='/tmp/…/linked'`. При заявленном несуществующем корне возвращает `root='/definitely/not/here', exists=False` |
| P-f | containment по родителю пропускает финальный симлинк | **ПОДТВЕРЖДЕНА** | `commonpath` на пути с неразрешённым последним звеном → `True`; `realpath` того же пути ведёт наружу репозитория; `open()` реально прочитал байты внешнего файла |
| P-i | механизм кольца работает (см. §2) | **ПОДТВЕРЖДЕНА** | bump, `occurrence_count=2`, кольцо длиной 1, эскалация severity |

**P-g2 — уточнение, которое перемер сделал ПРОТИВ моего же удобства.** П8 v2 утверждала
обратную ловушку: кто уплощит якорь (`meta.<key>_commit = "abc…"`), сделает значение
вырезаемым и сменит identity всех несущих строк. Измерено — утверждение верно **только
при двух условиях одновременно**:

```
значение = 40-hex коммит:   ПЛОСКИЙ ключ -> отпечаток НЕ меняется
                            (hex-прогон и так вырезается общим правилом CB-43)
значение = не-hex (слаг ветки), И описание его ЦИТИРУЕТ:
    без meta        auto:v1:f5fa7da7c11c632f2f666e210d2d87e2
    ПЛОСКИЙ ключ    auto:v1:12251770522c65bc740852911273727c   <- отпечаток УЕХАЛ
    ВЛОЖЕННЫЙ dict  auto:v1:f5fa7da7c11c632f2f666e210d2d87e2   <- не уехал
```

То есть: ловушка реальна, но требует плоского ключа с volatile-токеном **И** не-hex
значения, **И** описания, которое это значение цитирует. Для коммит-хеша — не работает
вовсе. Формулировка П8 в v2 была шире, чем правда; v3 обязана нести уточнённую.

---

## 8. Что каждое число РЕШАЕТ в v3

| число | решает |
|---|---|
| ре-наблюдений 0/129 и 0/3300, из них 0/240 после CB-43 | хранение: per-occurrence обосновывать нечем — популяции нет; ★ возвращается к top-level |
| механизм кольца РАБОТАЕТ (P-i) | ноль — про использование, а не про поломку; отсрочка per-occurrence честна, отказ был бы неверен |
| P-a: кольцо недостижимо | тумбстоун и ручное ре-якорение возможны ТОЛЬКО на верхнем уровне; при ринг-first читателе они мертвы (FATAL v2) |
| резолвер уже получает `file`, `description`, `meta` | захвату не нужно НИ ОДНОЙ правки сигнатур ядра, ни седьмого реестра, ни четвёртого opt-out флага |
| захват 0.19–0.67 мс против 8–16 мс, уже удерживаемых локом | двухфазность не окупается: она покупает 2–8% ценой нового шва на незащищённой стороне границы отпечатка |
| 609/709 строк не называют файла; согласие колонки 41/6 и 22/1 | грамматика строится вокруг колонки `file` + голой спеки, а `file:N` — подтверждающая, не ведущая ветвь |
| `site` 15/15 против `sites` 23/32 многофайловых | `site` и `sites` — РАЗНЫЕ ветви; «та же обработка» из v2 неверна |
| много-МЕСТ 47–54% / много-ФАЙЛОВ 5% | потеря от «первого места» — внутри одного файла, а не между файлами; цена конвенции измерена, 30/32 не подтвердилось |
| контекст 25.8%→3.82% против пролёта 25.8%→6.6% | `context_hash` обязателен и является основным различителем |
| 14.4% неуникальны даже при 60+ символах | порог длины не может быть механизмом уникальности; `ambiguous` — штатный исход |
| остаток 2.46–3.82% | у `ambiguous` есть измеренный пол — его нельзя обещать убрать |
| p99 = 130 KB, >1 MiB это 0.02% | `max_bytes_read = 1 MiB` — измеренная, а не выбранная граница |
| строк p99 = 3369, max = 135 926 | предел пролёта и почему «дочитать до строки N» нуждается в капе |
| P-f: симлинк | containment обязан считаться по `realpath` ПОЛНОГО пути, а не родителя |
| P-b: два разных корня | корень чтения — worktree-корень, `describe_root()` для этого непригоден |
| P-c: парсер 0% переиспользуем | ячейка цены C2 в v2 неверна; C2 не входит в ратифицируемое ядро |
| P-e: update без валидации | ре-якорение нуждается в валидаторе на пути записи, иначе `updatable_keys` — дыра |
| P-g2 уточнение | запрет на уплощение формулируется точно, а не шире правды |
| запись кольца = 250 байт; первые 10 не вытесняются | цена per-occurrence, когда до неё дойдёт черёд, считается от этого числа |

---

## 9. Сверка с числами v2 — что подтвердилось, что сдвинулось, что опровергнуто

**Подтвердилось точно:** распределение длин `list[int]` (206 строк, построчно);
`lines` codebugs 44; `sites` codebugs 18; `anchor` в autosorter = id карт;
`loc` свободен; форма `[0,0]` в корпусе отсутствует; парсеров в пакете нет;
базнейм-гейт 19/32 (замер Opus воспроизведён).

**Сдвинулось из-за роста корпуса** (не ошибка v2, но цифры нельзя цитировать как
сегодняшние): autosorter 3176 → **3300**; `lines` 539 → **559**; `sites` 30 → **36**;
`site` 13 → **16**; `anchor` 16 → **18**; codebugs 128/130 → **129**.

**ОПРОВЕРГНУТО перемером:**
1. «мульти-МЕСТО 30/32» — не воспроизводится ни при одном из трёх определений (23/12/24).
2. «`sites`/`site` — та же обработка, что `lines`» — они `path:N`-ветвь, и это две
   разные ветви между собой (`site` 0% многофайловых, `sites` 63%).
3. «заякорить может ~2787/3176» плюс ~381 — суммы не сходились; верно 2907 + 393 = 3300.
4. «реальный максимум в корпусе — строка 5708» — сегодня **7412**.
5. Ловушка уплощения П8 — шире правды (см. P-g2).
6. «ринг наследует import-strip» — наследует ПОЛНУЮ потерю: кольцо срезается целиком.
7. Ячейка цены C2 «парсер уже есть» — переиспользование 0%.

**Опровергнуто в сторону УСЛОЖНЕНИЯ дизайна, а не упрощения** (записываю отдельно,
потому что это проверка на подгонку): остаточная неоднозначность 2.5–3.8% не исчезает
ни при каком пролёте; 14.4% строк неуникальны даже при 60+ символах; 3% строк autosorter
несут два локационных ключа сразу и требуют таблицы приоритетов, которой у v2 не было;
197 + 167 + 22 + 7 = 393 строки autosorter вообще не указывают на читаемый файл.

---

## 10. Честные пределы этого перемера

1. **Кэш страниц тёплый.** 0.192 мс/файл — это тёплое чтение. Холодное я НЕ мерил:
   сброс кэша требует прав root. Значит вывод «захват дешевле того, что лок уже держит»
   верен для рабочей машины разработчика, где файл только что редактировали, и НЕ
   доказан для холодного старта. Пессимистичная оценка при 1 мс/файл на холодную:
   батч из 100 членов, трогающий 100 РАЗНЫХ путей, добавит ~100 мс к 125–1337 мс — то
   есть вывод переживает и пессимизм, но это оценка, а не замер.
2. **Классификация колонки `file` — эвристика** (`isdir`/`isfile`/глоб-символы/длина).
   Порядок величины устойчив, третья цифра — нет.
3. **Файловая перепись включает вендорные и сгенерированные деревья** autosorter
   (58 914 файлов). Для КАПА это правильная популяция — трекер может указать на любой
   из них, — но как «профиль исходников проекта» её читать нельзя.
4. **Сэмплирование позиций через одну из двадцати** в §6.2. При n ≈ 1.01 млн доверие к
   первым двум цифрам процентов есть; к третьей — нет.
5. **Цены измерены на одной машине, одним прогоном на точку.** Разброс не мерялся;
   опираться следует на ПОРЯДОК (0.2 мс против 10 мс), а не на конкретные значения.
6. **Обе базы — трекеры одного владельца.** Утверждение «ре-наблюдений не бывает»
   справедливо для ЭТОЙ практики использования, а не для трекеров вообще; появление
   автоматического филера меняет число, и v3 обязана назвать это триггером.

---

## 11. Приложение: скрипты дословно

Лежали при прогоне в `<scratchpad>/bt7-measure/`. Приведены целиком, чтобы любой
рецензент мог перевыполнить замер, а не поверить таблицам.

### `measure.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 remeasurement (canon К-5). Read-only over two exported corpora.

Run:
  uv run codebugs export-csv <OUT>/codebugs-live-<date>.csv
  uv run codebugs --tracker-root /home/faxik/w/autosorter export-csv <OUT>/autosorter-live-<date>.csv
  python3 measure.py <OUT>/codebugs-live-<date>.csv /home/faxik/w/codebugs \
                     <OUT>/autosorter-live-<date>.csv /home/faxik/w/autosorter
"""

import csv
import json
import os
import re
import sys
from collections import Counter

csv.field_size_limit(10**9)

LOC_KEYS = ["lines", "line", "sites", "site", "function", "location", "anchor", "loc"]
FILE_TOKEN = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+):(\d+)")
INTLIKE = re.compile(r"^\s*\d+\s*$")


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                r["_meta"] = json.loads(r["meta"]) if r["meta"] else {}
            except Exception:
                r["_meta"] = {}
            if not isinstance(r["_meta"], dict):
                r["_meta"] = {}
            rows.append(r)
    return rows


def shape(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def flatten_text(v):
    """Every string leaf of a locational value, for token scanning."""
    if isinstance(v, str):
        return [v]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [str(v)]
    if isinstance(v, list):
        out = []
        for x in v:
            out.extend(flatten_text(x))
        return out
    if isinstance(v, dict):
        out = []
        for x in v.values():
            out.extend(flatten_text(x))
        return out
    return []


def classify_file(val, root):
    if not val:
        return "empty"
    if any(ch in val for ch in "*?[") or val.endswith("/"):
        return "glob_or_dir_notation"
    p = val if os.path.isabs(val) else os.path.join(root, val)
    try:
        if os.path.isdir(p):
            return "directory"
        if os.path.isfile(p):
            return "regular_file"
    except OSError:
        return "stat_error"
    # not on disk: is it even path-shaped?
    if " " in val.strip() or len(val) > 200:
        return "prose_or_nonpath"
    return "missing_path"


def report(name, rows, root, snapshot_note):
    print(f"\n{'=' * 78}\n## {name}  ({snapshot_note})\n{'=' * 78}")
    n = len(rows)
    print(f"M1 rows: {n}")

    # ---- M2 re-observation --------------------------------------------------
    oc = Counter()
    ring_rows = 0
    ring_len = Counter()
    for r in rows:
        try:
            c = int(r.get("occurrence_count") or 0)
        except ValueError:
            c = 0
        oc[c] += 1
        ring = r["_meta"].get("occurrences")
        if isinstance(ring, list) and ring:
            ring_rows += 1
            ring_len[len(ring)] += 1
    gt1 = sum(v for k, v in oc.items() if k > 1)
    print(f"M2 occurrence_count distribution: {dict(sorted(oc.items()))}")
    print(f"M2 rows with occurrence_count>1: {gt1}/{n}")
    print(f"M2 rows carrying a non-empty meta.occurrences ring: {ring_rows}/{n}  lens={dict(ring_len)}")
    post = [r for r in rows if (r.get("created_at") or "") >= "2026-08-16"]
    post_gt1 = sum(1 for r in post if (int(r.get("occurrence_count") or 0)) > 1)
    print(f"M2 rows created on/after 2026-08-16 (CB-43 landed): {len(post)}; of those occurrence_count>1: {post_gt1}")
    print(f"M2 newest created_at in corpus: {max((r.get('created_at') or '') for r in rows)}")

    # ---- M3 key census ------------------------------------------------------
    print("M3 locational key census (top-level meta key -> rows, value shapes):")
    for k in LOC_KEYS:
        holders = [r for r in rows if k in r["_meta"]]
        if not holders:
            print(f"    {k:9s}: 0")
            continue
        shapes = Counter(shape(r["_meta"][k]) for r in holders)
        print(f"    {k:9s}: {len(holders):4d}   shapes={dict(shapes)}")

    # ---- M4 lines-list length distribution ----------------------------------
    lens = Counter()
    allint = 0
    for r in rows:
        v = r["_meta"].get("lines")
        if isinstance(v, list):
            lens[len(v)] += 1
            if v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
                allint += 1
    print(f"M4 meta.lines list lengths: {dict(sorted(lens.items()))}; lists that are all-int: {allint}")

    # ---- M5 multi-key rows --------------------------------------------------
    combos = Counter()
    multi = 0
    for r in rows:
        present = tuple(k for k in LOC_KEYS if k in r["_meta"])
        if len(present) >= 2:
            multi += 1
            combos[present] += 1
    print(f"M5 rows carrying >=2 locational keys: {multi}")
    for c, cnt in combos.most_common():
        print(f"    {'+'.join(c):40s} {cnt}")

    # ---- M6/M7 file:N token analysis, per key -------------------------------
    print("M6 file:N token analysis (per key: rows whose value carries NAME.ext:N):")
    for k in ("lines", "line", "sites", "site", "location", "function"):
        holders = [r for r in rows if k in r["_meta"]]
        if not holders:
            continue
        tokened = []
        for r in holders:
            texts = flatten_text(r["_meta"][k])
            toks = []
            for t in texts:
                toks.extend(FILE_TOKEN.findall(t))
            if toks:
                tokened.append((r, toks))
        if not tokened:
            print(f"    {k:9s}: rows={len(holders):4d} with-token=0")
            continue
        distinct_names = Counter()
        gate_pass = gate_mismatch = gate_ambiguous = 0
        fullpath_exact = 0
        for r, toks in tokened:
            names = {t[0] for t in toks}
            distinct_names[len(names)] += 1
            col = r.get("file") or ""
            if len(names) == 1:
                nm = next(iter(names))
                if os.path.basename(col) == os.path.basename(nm):
                    gate_pass += 1
                else:
                    gate_mismatch += 1
                if col == nm:
                    fullpath_exact += 1
            else:
                gate_ambiguous += 1
        print(
            f"    {k:9s}: rows={len(holders):4d} with-token={len(tokened):4d} "
            f"| BASENAME-GATE pass={gate_pass} mismatch={gate_mismatch} ambiguous(multi-filename)={gate_ambiguous} "
            f"| full-path-exact={fullpath_exact} | distinct-filenames-per-row={dict(sorted(distinct_names.items()))}"
        )

    # ---- M8 multi-place / multi-file, codebugs-style -------------------------
    multiplace = multifile = tokened_rows = 0
    for r in rows:
        v = r["_meta"].get("lines")
        if v is None:
            continue
        toks = []
        for t in flatten_text(v):
            toks.extend(FILE_TOKEN.findall(t))
        if not toks:
            continue
        tokened_rows += 1
        if len(toks) > 1:
            multiplace += 1
        if len({t[0] for t in toks}) > 1:
            multifile += 1
    print(f"M8 meta.lines rows with a file:N token: {tokened_rows}; multi-PLACE {multiplace}; multi-FILE {multifile}")

    # ---- M9 file column population ------------------------------------------
    cls = Counter(classify_file(r.get("file") or "", root) for r in rows)
    print(f"M9 `file` column classification against {root}: {dict(cls.most_common())}")

    # ---- M10 loc-neighbourhood ----------------------------------------------
    neigh = Counter()
    for r in rows:
        for k in r["_meta"]:
            if "loc" in k.lower():
                neigh[k] += 1
    print(f"M10 meta keys containing 'loc': {dict(neigh.most_common())}")

    # ---- M11 largest referenced line number ---------------------------------
    biggest = 0
    for r in rows:
        for k in LOC_KEYS:
            v = r["_meta"].get(k)
            if v is None:
                continue
            for t in flatten_text(v):
                for m in re.findall(r"\d+", t):
                    if len(m) <= 7:
                        biggest = max(biggest, int(m))
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, int) and not isinstance(x, bool):
                        biggest = max(biggest, x)
    print(f"M11 largest integer appearing in any locational value: {biggest}")

    # ---- M12 zero/negative line values --------------------------------------
    zero_rows = 0
    for r in rows:
        v = r["_meta"].get("lines")
        if isinstance(v, list) and v and all(isinstance(x, int) and x < 1 for x in v):
            zero_rows += 1
    print(f"M12 meta.lines lists whose every element is <1 (the [0,0] shape): {zero_rows}")


def main():
    cb_csv, cb_root, au_csv, au_root = sys.argv[1:5]
    report("codebugs (live)", load(cb_csv), cb_root, os.path.basename(cb_csv))
    report("autosorter (live)", load(au_csv), au_root, os.path.basename(au_csv))


if __name__ == "__main__":
    main()
```

### `measure2.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 remeasurement, pass 2 (canon К-5): value SHAPES for the grammar table,
and the multi-place count under three explicit definitions.

Usage: python3 measure2.py <codebugs.csv> <autosorter.csv>
"""

import csv
import json
import re
import sys
from collections import Counter

csv.field_size_limit(10**9)

FILE_TOKEN = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+):(\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)")
BARE_INT = re.compile(r"^\s*\d+\s*$")
BARE_RANGE = re.compile(r"^\s*\d+\s*-\s*\d+\s*$")
BARE_LIST = re.compile(r"^\s*\d+(\s*(,|;)\s*\d+(\s*-\s*\d+)?)+\s*$")
PATH_COLON = re.compile(r"[A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+\s*:")


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                m = json.loads(r["meta"]) if r["meta"] else {}
            except Exception:
                m = {}
            r["_meta"] = m if isinstance(m, dict) else {}
            rows.append(r)
    return rows


def classify_str(s):
    t = s.strip()
    if BARE_INT.fullmatch(t):
        return "bare_int_string"
    if BARE_RANGE.fullmatch(t):
        return "bare_range 'a-b'"
    if BARE_LIST.fullmatch(t):
        return "bare_list 'N,M,K-L'"
    if PATH_COLON.search(t):
        return "path:N token(s)"
    if re.search(r"\d", t):
        return "prose_with_digits"
    return "prose_no_digits"


def places_in_value(v):
    """Three definitions of 'how many distinct places does this value name'."""
    texts = []

    def walk(x):
        if isinstance(x, str):
            texts.append(x)
        elif isinstance(x, bool):
            pass
        elif isinstance(x, int):
            texts.append(str(x))
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)

    walk(v)
    joined = " ; ".join(texts)
    d1_tokens = FILE_TOKEN.findall(joined)  # D1: file:N tokens
    d1 = len(d1_tokens)
    # D2: distinct filenames named
    d2 = len({t[0] for t in d1_tokens})
    # D3: atomic line references (a range counts as ONE place; each comma item one place)
    d3 = 0
    for _, spec in d1_tokens:
        d3 += len([p for p in re.split(r"\s*,\s*", spec) if p.strip()])
    if not d1_tokens:
        # no file token: count comma/semicolon separated numeric groups, or list members
        if isinstance(v, list):
            d3 = len(v)
            d1 = len(v)
        else:
            parts = [p for p in re.split(r"\s*[,;]\s*", str(v)) if re.search(r"\d", p)]
            d3 = len(parts)
            d1 = len(parts)
    return d1, d2, d3


def report(name, rows):
    print(f"\n{'=' * 78}\n## {name}\n{'=' * 78}")
    for key in ("lines", "line", "sites", "site", "location", "function"):
        holders = [r for r in rows if key in r["_meta"]]
        if not holders:
            continue
        strc = Counter()
        listc = Counter()
        other = Counter()
        for r in holders:
            v = r["_meta"][key]
            if isinstance(v, str):
                strc[classify_str(v)] += 1
            elif isinstance(v, list):
                if v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
                    listc["list[int]"] += 1
                elif v and all(isinstance(x, str) for x in v):
                    listc["list[str] " + classify_str(v[0])] += 1
                else:
                    listc["list[mixed/other]"] += 1
            elif isinstance(v, bool):
                other["bool"] += 1
            elif isinstance(v, int):
                other["int"] += 1
            else:
                other[type(v).__name__] += 1
        print(f"\n  {key} — {len(holders)} rows")
        for k, c in strc.most_common():
            print(f"      str  {k:24s} {c}")
        for k, c in listc.most_common():
            print(f"      list {k:24s} {c}")
        for k, c in other.most_common():
            print(f"      {k:29s} {c}")

    print("\n  MULTI-PLACE, three definitions (over rows carrying `lines`):")
    d1c = Counter()
    d2c = Counter()
    d3c = Counter()
    n = 0
    for r in rows:
        v = r["_meta"].get("lines")
        if v is None:
            continue
        n += 1
        a, b, c = places_in_value(v)
        d1c[a > 1] += 1
        d2c[b > 1] += 1
        d3c[c > 1] += 1
    print(f"      rows with `lines`: {n}")
    print(f"      D1 >1 file:N token OR >1 list member/comma group : {d1c[True]}")
    print(f"      D2 >1 DISTINCT FILENAME named                    : {d2c[True]}")
    print(f"      D3 >1 atomic line reference (range = one place)  : {d3c[True]}")

    print("\n  Samples of the shapes that decide the grammar:")
    shown = Counter()
    for r in rows:
        for key in ("sites", "site", "lines"):
            v = r["_meta"].get(key)
            if v is None:
                continue
            kind = classify_str(v) if isinstance(v, str) else ("list" if isinstance(v, list) else type(v).__name__)
            tag = f"{key}/{kind}"
            if shown[tag] < 1:
                shown[tag] += 1
                s = json.dumps(v, ensure_ascii=False)
                print(f"      {tag:34s} {s[:110]}")


def main():
    report("codebugs (live)", load(sys.argv[1]))
    report("autosorter (live)", load(sys.argv[2]))


if __name__ == "__main__":
    main()
```

### `measure3.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 remeasurement, pass 3 (canon К-5): the final grammar table numbers.

Fixes pass-2's classifier (a comma list may LEAD with a range) and publishes every
regex it uses, so the numbers are reproducible rather than classifier-dependent prose.

Usage: python3 measure3.py <codebugs.csv> <autosorter.csv>
"""

import csv
import json
import os
import re
import sys
from collections import Counter

csv.field_size_limit(10**9)

# --- published grammar regexes -------------------------------------------------
R_INT = re.compile(r"\d+")
R_SPAN = re.compile(r"\d+\s*-\s*\d+")
R_ITEM = re.compile(r"\d+\s*-\s*\d+|\d+")                    # one atomic place
R_SPEC = re.compile(r"(?:\d+\s*-\s*\d+|\d+)(?:\s*,\s*(?:\d+\s*-\s*\d+|\d+))*")
R_BARE = re.compile(r"\s*(?:\d+\s*-\s*\d+|\d+)(?:\s*,\s*(?:\d+\s*-\s*\d+|\d+))*\s*")
R_NAME = re.compile(r"[A-Za-z0-9_.\-/]*[A-Za-z0-9_\-]\.[A-Za-z0-9_]+")
R_TOKEN = re.compile(rf"({R_NAME.pattern})\s*:\s*({R_SPEC.pattern})")

KEYS = ("lines", "line", "sites", "site", "location", "function")


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                m = json.loads(r["meta"]) if r["meta"] else {}
            except Exception:
                m = {}
            r["_meta"] = m if isinstance(m, dict) else {}
            rows.append(r)
    return rows


def strings_of(v):
    out = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, int):
            out.append(str(x))
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)

    walk(v)
    return out


def classify(v):
    """Grammar branch for ONE meta value. Order is the proposed precedence."""
    if isinstance(v, bool):
        return "B0_unusable(bool)"
    if isinstance(v, int):
        return "B1_bare_int"
    if isinstance(v, list):
        if v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            return "B2_list_of_int"
        if v and all(isinstance(x, str) for x in v):
            if any(R_TOKEN.search(x) for x in v):
                return "B3_list_of_path_tokens"
            return "B6_prose"
        return "B0_unusable(mixed_list)"
    if isinstance(v, dict):
        if any(R_TOKEN.search(s) for s in strings_of(v)):
            return "B3_list_of_path_tokens(dict)"
        if any(R_ITEM.search(s) for s in strings_of(v)):
            return "B5_dict_of_specs"
        return "B6_prose"
    if not isinstance(v, str):
        return "B0_unusable(other)"
    t = v.strip()
    if R_TOKEN.search(t):
        return "B3_path_token(s)"
    if R_BARE.fullmatch(t):
        return "B4_bare_spec"
    if R_INT.search(t):
        return "B6_prose_with_digits"
    return "B6_prose"


def places(v):
    """(tokens, distinct_filenames, atomic_places) for one value."""
    joined = " ; ".join(strings_of(v))
    toks = R_TOKEN.findall(joined)
    if toks:
        atomic = sum(len(R_ITEM.findall(spec)) for _, spec in toks)
        return len(toks), len({n for n, _ in toks}), atomic
    if isinstance(v, list):
        return len(v), 0, len(v)
    atomic = len(R_ITEM.findall(joined))
    return atomic, 0, atomic


def report(name, rows, root):
    print(f"\n{'=' * 78}\n## {name}   rows={len(rows)}\n{'=' * 78}")

    print("\nG1 grammar branch per key (published regexes; see script head):")
    for key in KEYS:
        holders = [r for r in rows if key in r["_meta"]]
        if not holders:
            continue
        c = Counter(classify(r["_meta"][key]) for r in holders)
        print(f"  {key:9s} n={len(holders):4d}  " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))

    print("\nG2 basename gate, per key (single filename named AND it matches basename(file column)):")
    for key in KEYS:
        holders = [r for r in rows if key in r["_meta"]]
        if not holders:
            continue
        tok_rows = 0
        one_name = pass_ = mism = amb = fullpath = 0
        for r in holders:
            toks = R_TOKEN.findall(" ; ".join(strings_of(r["_meta"][key])))
            if not toks:
                continue
            tok_rows += 1
            names = {n for n, _ in toks}
            col = r.get("file") or ""
            if len(names) == 1:
                one_name += 1
                nm = next(iter(names))
                if os.path.basename(col) == os.path.basename(nm):
                    pass_ += 1
                else:
                    mism += 1
                if col == nm:
                    fullpath += 1
            else:
                amb += 1
        if tok_rows:
            print(
                f"  {key:9s} with-token={tok_rows:4d}  single-filename={one_name:4d}  "
                f"GATE-PASS={pass_:4d}  mismatch={mism:3d}  multi-filename={amb:4d}  full-path-exact={fullpath}"
            )

    print("\nG3 how many PLACES does one value name (rows carrying the key):")
    for key in ("lines", "sites", "site", "line"):
        holders = [r for r in rows if key in r["_meta"]]
        if not holders:
            continue
        multi_place = multi_file = 0
        dist = Counter()
        for r in holders:
            _, nf, atomic = places(r["_meta"][key])
            dist[min(atomic, 10)] += 1
            if atomic > 1:
                multi_place += 1
            if nf > 1:
                multi_file += 1
        print(
            f"  {key:9s} n={len(holders):4d}  multi-PLACE={multi_place:4d} "
            f"({100 * multi_place // max(len(holders), 1)}%)  multi-FILE={multi_file:4d} "
            f"({100 * multi_file // max(len(holders), 1)}%)  atomic-count dist(capped at 10)={dict(sorted(dist.items()))}"
        )

    print("\nG4 UNION over all locational keys — one row, one question: how many files does it name?")
    nf_dist = Counter()
    any_loc = 0
    for r in rows:
        names = set()
        has = False
        for key in KEYS:
            if key in r["_meta"]:
                has = True
                for n, _ in R_TOKEN.findall(" ; ".join(strings_of(r["_meta"][key]))):
                    names.add(os.path.basename(n))
        if has:
            any_loc += 1
            nf_dist[len(names)] += 1
    print(f"  rows carrying ANY locational key: {any_loc};  distinct filenames named: {dict(sorted(nf_dist.items()))}")

    print("\nG5 does the row's `file` column agree with the single filename named? (rows naming exactly one)")
    agree = disagree = 0
    for r in rows:
        names = set()
        for key in KEYS:
            if key in r["_meta"]:
                for n, _ in R_TOKEN.findall(" ; ".join(strings_of(r["_meta"][key]))):
                    names.add(os.path.basename(n))
        if len(names) == 1:
            if os.path.basename(r.get("file") or "") == next(iter(names)):
                agree += 1
            else:
                disagree += 1
    print(f"  agree={agree}  disagree={disagree}")


def main():
    report("codebugs (live)", load(sys.argv[1]), "/home/faxik/w/codebugs")
    report("autosorter (live)", load(sys.argv[2]), "/home/faxik/w/autosorter")


if __name__ == "__main__":
    main()
```

### `probe_premises.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 (canon К-5): verify the load-bearing CODE premises BY EXECUTION.

Every check below decides a design clause of BT-7 v3. Nothing here touches a live
tracker: each probe builds its own tracker in a fresh temp dir.

Run from the repo root:  PYTHONPATH=src python3 probe_premises.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from codebugs import db, findings  # noqa: E402

OK, BAD = "CONFIRMED", "REFUTED"


def fresh():
    d = tempfile.mkdtemp(prefix="bt7probe-")
    db.set_tracker_root(None)
    os.environ.pop("CODEBUGS_ROOT", None)
    db.init_project(d)
    conn = db.connect(project_dir=d)
    return d, conn


def add(conn, **kw):
    kw.setdefault("severity", "low")
    kw.setdefault("category", "test_probe")
    kw.setdefault("file", "a.py")
    kw.setdefault("description", "probe " + str(time.time_ns()))
    kw.setdefault("new_category", True)
    return findings.add_finding(conn, **kw)


def p(label, verdict, detail):
    print(f"\n[{verdict}] {label}\n    {detail}")


# ---------------------------------------------------------------- P-a ring write
def probe_ring_unreachable():
    d, conn = fresh()
    try:
        r = add(conn)
        fid = r["id"] if isinstance(r, dict) else r
        fid = fid["id"] if isinstance(fid, dict) else fid
        try:
            findings.update_finding(conn, finding_id=fid, meta_update={"occurrences": [{"x": 1}]})
            p("P-a update_finding(meta_update={'occurrences':...})", BAD, "it was ACCEPTED")
        except Exception as e:
            p("P-a update_finding(meta_update={'occurrences':...})", OK,
              f"refused: {type(e).__name__}: {str(e)[:150]}")
        print(f"    _RESERVED_META_KEYS = {sorted(findings._RESERVED_META_KEYS)}")
        print(f"    db.resolver_updatable_meta_keys() = {sorted(db.resolver_updatable_meta_keys())}")
        print(f"    db.resolver_reserved_meta_keys()  = {sorted(db.resolver_reserved_meta_keys())}")
        print(f"    _OCC_KEEP_FIRST={findings._OCC_KEEP_FIRST} _OCC_KEEP_LAST={findings._OCC_KEEP_LAST} "
              f"_OCC_DESC_CAP={findings._OCC_DESC_CAP}")
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------- P-h/P-e reservation & validation
def probe_reservation():
    d, conn = fresh()
    try:
        try:
            add(conn, meta={"similar_to": [{"id": "X"}]})
            p("P-h add(meta={'similar_to':...}) — registration IS reservation", BAD, "ACCEPTED")
        except Exception as e:
            p("P-h add(meta={'similar_to':...}) — registration IS reservation", OK,
              f"refused: {type(e).__name__}: {str(e)[:150]}")
        r = add(conn)
        fid = r["id"] if isinstance(r, dict) else r
        try:
            out = findings.update_finding(
                conn, finding_id=fid,
                meta_update={"similar_to": "NOT-A-LIST-AT-ALL", "bogus_nested": {"line": -5, "v": 99}})
            got = json.loads(out["meta"]) if isinstance(out.get("meta"), str) else out.get("meta")
            p("P-e updatable_keys opens update with NO validation", OK,
              f"ACCEPTED garbage; stored similar_to={got.get('similar_to')!r}")
        except Exception as e:
            p("P-e updatable_keys opens update with NO validation", BAD,
              f"refused: {type(e).__name__}: {str(e)[:150]}")
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- P-g fingerprint
def probe_fingerprint_order():
    import inspect
    src = inspect.getsource(findings._add_one)
    i_fp = src.find("_derive_fingerprint")
    i_rs = src.find("run_pre_add_resolvers")
    p("P-g fingerprint derived BEFORE resolvers (Р1's structural guarantee)",
      OK if (i_fp != -1 and i_rs != -1 and i_fp < i_rs) else BAD,
      f"_add_one source offsets: _derive_fingerprint@{i_fp}, run_pre_add_resolvers@{i_rs}")
    n = findings._normalize_for_fingerprint
    base = {"category": "c", "file": "f", "description": "d"}
    flat = n(dict(base, meta={"loc_commit": "abc123def456"}))
    nested = n(dict(base, meta={"loc": {"commit": "abc123def456", "line": 5}}))
    plain = n(dict(base, meta={}))
    print(f"    _VOLATILE_KEY_TOKENS = {sorted(findings._VOLATILE_KEY_TOKENS)}")
    print(f"    normalize(meta={{}})                      -> {str(plain)[:120]}")
    print(f"    normalize(meta={{'loc_commit': <hex>}})     -> {str(flat)[:120]}")
    print(f"    normalize(meta={{'loc': {{...}}}}) (nested)   -> {str(nested)[:120]}")
    print(f"    FLATTENED key changes normalization: {flat != plain}; NESTED dict changes it: {nested != plain}")


# ---------------------------------------------------------------- P-d import ring
def probe_import_strips_ring():
    import inspect
    src = inspect.getsource(findings._import_meta)
    p("P-d import strips reserved keys at TOP LEVEL only (so a ring is lost whole)",
      OK if "occurrences" not in src.split("def")[0] else "SEE-SOURCE",
      "source of _import_meta:\n        " + "\n        ".join(src.strip().splitlines()[:22]))


# ---------------------------------------------------------------- P-c rename parse
def probe_rename_parser():
    from codebugs import provenance
    d = tempfile.mkdtemp(prefix="bt7git-")
    try:
        run = lambda *a: subprocess.run(a, cwd=d, capture_output=True, text=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "p@p")
        run("git", "config", "user.name", "p")
        with open(os.path.join(d, "old.py"), "w") as fh:
            fh.write("\n".join(f"line {i}" for i in range(1, 40)) + "\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "one")
        base = run("git", "rev-parse", "HEAD").stdout.strip()
        os.rename(os.path.join(d, "old.py"), os.path.join(d, "new.py"))
        with open(os.path.join(d, "new.py"), "w") as fh:
            fh.write("\n".join(f"line {i}" for i in range(1, 45)) + "\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "two")
        hunk = run("git", "diff", "-U0", "--find-renames", f"{base}..HEAD").stdout
        namestatus = run("git", "diff", "--name-status", "-z", "--diff-filter=R", f"{base}..HEAD").stdout
        parsed_hunk = provenance._parse_rename_records(hunk)
        parsed_ns = provenance._parse_rename_records(namestatus)
        p("P-c _parse_rename_records CANNOT read `-U0 --find-renames` hunk output",
          OK if parsed_hunk is None or parsed_hunk == [] else BAD,
          f"on -U0 output -> {parsed_hunk!r};  on --name-status -z output -> {parsed_ns!r}")
        print("    first 5 lines of the -U0 output the design proposes to parse:")
        for ln in hunk.splitlines()[:5]:
            print(f"        {ln}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- P-b root identity
def probe_roots():
    from codebugs import provenance
    d = tempfile.mkdtemp(prefix="bt7wt-")
    try:
        main = os.path.join(d, "main")
        os.makedirs(main)
        run = lambda *a, cwd=main: subprocess.run(a, cwd=cwd, capture_output=True, text=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "p@p")
        run("git", "config", "user.name", "p")
        open(os.path.join(main, "f.py"), "w").write("x = 1\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "one")
        db.set_tracker_root(None)
        db.init_project(main)
        wt = os.path.join(d, "linked")
        run("git", "worktree", "add", "-q", "-b", "fix/probe", wt)
        cwd0 = os.getcwd()
        try:
            os.chdir(wt)
            db.set_tracker_root(None)
            desc = db.describe_root()
            wtroot = provenance._repo_root(wt)
            p("P-b describe_root() is the TRACKER root, NOT the worktree root",
              OK if os.path.realpath(desc.get("root") or "") != os.path.realpath(wtroot or "") else BAD,
              f"describe_root().root = {desc.get('root')!r} (source={desc.get('source')!r})\n"
              f"    provenance._repo_root(linked worktree) = {wtroot!r}\n"
              f"    cwd was {wt!r}")
        finally:
            os.chdir(cwd0)
        db.set_tracker_root("/definitely/not/here")
        try:
            desc2 = db.describe_root()
            print(f"    describe_root() with a bogus declared root -> root={desc2.get('root')!r} "
                  f"exists={desc2.get('exists')!r} error={str(desc2.get('error'))[:60]!r}")
        finally:
            db.set_tracker_root(None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- P-f symlink
def probe_symlink_containment():
    import os.path as op
    d = tempfile.mkdtemp(prefix="bt7link-")
    try:
        repo = op.join(d, "repo")
        os.makedirs(repo)
        secret = op.join(d, "SECRET.txt")
        open(secret, "w").write("top secret bytes\n")
        link = op.join(repo, "innocent.py")
        os.symlink(secret, link)
        parent_resolved = op.join(op.realpath(op.dirname(link)), op.basename(link))
        full_resolved = op.realpath(link)
        contained_parent = op.commonpath([op.realpath(repo), parent_resolved]) == op.realpath(repo)
        contained_full = op.commonpath([op.realpath(repo), full_resolved]) == op.realpath(repo)
        data = open(link).read().strip()
        p("P-f containment on the PARENT-resolved path admits a final symlink out of the repo",
          OK if (contained_parent and not contained_full) else BAD,
          f"parent-resolved={parent_resolved!r} contained={contained_parent}\n"
          f"    full realpath={full_resolved!r} contained={contained_full}\n"
          f"    open(link) actually read: {data!r}  <- these are the bytes a capture would persist")
    finally:
        shutil.rmtree(d, ignore_errors=True)


for f in (probe_ring_unreachable, probe_reservation, probe_fingerprint_order,
          probe_import_strips_ring, probe_rename_parser, probe_roots, probe_symlink_containment):
    try:
        f()
    except Exception as exc:  # a probe that cannot run is itself a finding
        p(f.__name__, "PROBE-ERROR", f"{type(exc).__name__}: {exc}")
```

### `probe_premises2.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 (canon К-5), probe pass 2: the flattening hazard (Р1) and the decisive
question the zero-population number leaves open — does the ring MECHANISM work at all,
or is the measured zero an artefact of a broken bump path?

Run from the repo root:  PYTHONPATH=src python3 probe_premises2.py
"""

import json
import os
import shutil
import sys
import tempfile
import time

from codebugs import db, findings


def fresh():
    d = tempfile.mkdtemp(prefix="bt7probe2-")
    db.set_tracker_root(None)
    os.environ.pop("CODEBUGS_ROOT", None)
    db.init_project(d)
    return d, db.connect(project_dir=d)


print("=" * 78)
print("P-g2  FLATTENING HAZARD: does a top-level str under a volatile-token key move")
print("      the fingerprint's normalized text, while a nested dict does not?")
print("=" * 78)
n = findings._normalize_for_fingerprint
desc = "anchor probe: resolve the thing at the place"
plain = n(desc, {})
flat = n(desc, {"loc_commit": "abc123def4567890abc123def4567890abc12345"})
nested = n(desc, {"loc": {"captured_at_commit": "abc123def4567890abc123def4567890abc12345", "line": 5}})
print(f"  _VOLATILE_KEY_TOKENS = {sorted(findings._VOLATILE_KEY_TOKENS)}")
print(f"  normalize(meta={{}})                            -> {plain!r}")
print(f"  normalize(meta={{'loc_commit': <40hex>}})         -> {flat!r}")
print(f"  normalize(meta={{'loc': {{...same hex nested}}}})   -> {nested!r}")
print(f"  FLATTENED key changes the normalized text : {flat != plain}")
print(f"  NESTED  dict changes the normalized text  : {nested != plain}")
print("  => the hazard is real but latent: only a FLAT str value is stripped, so an")
print("     anchor stored as a nested object cannot move the fingerprint, and an anchor")
print("     later FLATTENED would silently re-key every row carrying one.")

print()
print("=" * 78)
print("P-i   DOES THE RING MECHANISM WORK? (the zero-population number is about USAGE,")
print("      not about a broken bump path — this probe decides which)")
print("=" * 78)
d, conn = fresh()
try:
    a = findings.add_finding(
        conn, severity="low", category="probe_cat", file="a.py",
        description="a very specific defect in the widget loader that repeats", new_category=True)
    b = findings.add_finding(
        conn, severity="high", category="probe_cat", file="a.py",
        description="a very specific defect in the widget loader that repeats")
    fid = a["id"] if isinstance(a, dict) else a
    row = findings.get_finding(conn, finding_id=fid)
    meta = json.loads(row["meta"]) if isinstance(row["meta"], str) else (row["meta"] or {})
    ring = meta.get("occurrences") or []
    print(f"  add #1 -> id={a.get('id')} was_new={a.get('was_new')} action={a.get('dedup_action')}")
    print(f"  add #2 -> id={b.get('id')} was_new={b.get('was_new')} action={b.get('dedup_action')}")
    print(f"  row.occurrence_count = {row['occurrence_count']}")
    print(f"  ring length = {len(ring)}; ring entry keys = {sorted(ring[0]) if ring else '(none)'}")
    print(f"  severity after re-observation (escalation) = {row['severity']}")
    print("  => the mechanism WORKS. The measured 0/129 and 0/3300 are therefore a fact")
    print("     about how these trackers are USED (hand-filed distinct cards), not a bug.")

    print()
    print("  What a ring entry costs today, per entry (no anchor yet):")
    print(f"    entry bytes (json) = {len(json.dumps(ring[0])) if ring else 0}")
    print(f"    _OCC_DESC_CAP={findings._OCC_DESC_CAP}  keep_first={findings._OCC_KEEP_FIRST} "
          f"keep_last={findings._OCC_KEEP_LAST}")
    print("    NOTE: overflow keeps ring[:10] + ring[-10:] — the FIRST TEN are never evicted.")
finally:
    conn.close()
    shutil.rmtree(d, ignore_errors=True)

print()
print("=" * 78)
print("P-j   COST: what does one add cost today, and what would a file read+hash add?")
print("=" * 78)
d, conn = fresh()
try:
    # warm up
    findings.add_finding(conn, severity="low", category="probe_cat", file="a.py",
                         description="warmup " + str(time.time_ns()), new_category=True)
    N = 50
    t0 = time.perf_counter()
    for i in range(N):
        findings.add_finding(conn, severity="low", category="probe_cat", file="a.py",
                             description=f"cost probe distinct body number {i} " + "x" * 60)
    t1 = time.perf_counter()
    per_add_ms = (t1 - t0) * 1000 / N
    print(f"  add_finding x{N} (similarity resolver registered): {per_add_ms:.2f} ms/add")

    members = [{"severity": "low", "category": "probe_cat", "file": "a.py",
                "description": f"batch probe distinct body {i} " + "y" * 60} for i in range(100)]
    t0 = time.perf_counter()
    findings.batch_add_findings(conn, findings_list=members)
    t1 = time.perf_counter()
    print(f"  batch_add_findings(100 members): {(t1 - t0) * 1000:.1f} ms TOTAL "
          f"({(t1 - t0) * 10:.2f} ms/member)")
finally:
    conn.close()
    shutil.rmtree(d, ignore_errors=True)

# file read + hash cost, over this repository's own sources
import hashlib  # noqa: E402

repo = "/home/faxik/w/codebugs/src/codebugs"
files = [os.path.join(repo, f) for f in sorted(os.listdir(repo)) if f.endswith(".py")]
sizes = [os.path.getsize(f) for f in files]
t0 = time.perf_counter()
tot = 0
for f in files:
    with open(f, "rb") as fh:
        data = fh.read(1 << 20)
    lines = data.decode("utf-8", errors="replace").splitlines()
    seg = "\n".join(lines[100:112])
    hashlib.sha256(json.dumps([seg]).encode()).hexdigest()
    tot += len(data)
t1 = time.perf_counter()
print(f"  read+decode+splitlines+hash over {len(files)} real source files "
      f"({tot / 1024:.0f} KB total, max {max(sizes) / 1024:.0f} KB): "
      f"{(t1 - t0) * 1000:.1f} ms TOTAL = {(t1 - t0) * 1000 / len(files):.2f} ms/file (warm cache)")
print(f"  largest file in src/codebugs: {max(sizes) / 1024:.0f} KB; "
      f"line count of the largest: "
      f"{sum(1 for _ in open(files[sizes.index(max(sizes))], encoding='utf-8', errors='replace'))}")
```

### `probe_cost.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 (canon К-5): the COST numbers, measured here rather than inherited.

v2 carried `23.7 ms/add` and `~2.4 s per 100-member batch` as OTHER PEOPLE'S numbers,
and the whole two-phase-capture argument leans on them. This probe measures:
  (1) add cost against a REALISTIC pool (the similarity resolver's pool is newest-500,
      so cost depends on corpus size — an empty tracker measures nothing),
  (2) batch_add of 100 members,
  (3) what a file read + normalize + hash actually costs, warm and cold,
so the design can be argued against measured magnitudes.

Run from the repo root:  PYTHONPATH=src python3 probe_cost.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

from codebugs import db, findings, similarity


def fresh():
    d = tempfile.mkdtemp(prefix="bt7cost-")
    db.set_tracker_root(None)
    os.environ.pop("CODEBUGS_ROOT", None)
    db.init_project(d)
    return d, db.connect(project_dir=d)


def seed(conn, n, cat):
    """Fill the tracker so the similarity resolver's newest-500 pool is realistic."""
    members = [
        {
            "severity": "low",
            "category": cat,
            "file": f"src/mod_{i % 40}.py",
            "description": (
                f"seeded finding {i}: the handler in module {i % 40} mishandles the "
                f"third argument when the queue is drained concurrently, and the "
                f"resulting state is reported as success number {i}"
            ),
        }
        for i in range(n)
    ]
    findings.batch_add_findings(conn, members, new_category=True)


print(f"similarity.CANDIDATE_POOL_LIMIT = {similarity.CANDIDATE_POOL_LIMIT}")
print(f"similarity.DEFAULT_THRESHOLD    = {similarity.DEFAULT_THRESHOLD}")
print(f"similarity.MIN_TEXT_LEN         = {similarity.MIN_TEXT_LEN}")

for pool in (0, 200, 600, 1200):
    d, conn = fresh()
    try:
        if pool:
            seed(conn, pool, "cost_probe")
        N = 40
        # warm
        findings.add_finding(conn, severity="low", category="cost_probe", file="a.py",
                             description="warm " + "w" * 80, new_category=True)
        t0 = time.perf_counter()
        for i in range(N):
            findings.add_finding(
                conn, severity="low", category="cost_probe", file="a.py",
                description=f"timed distinct observation {i} about a handler that leaks a slot " + "z" * 40)
        t1 = time.perf_counter()
        per = (t1 - t0) * 1000 / N
        members = [
            {"severity": "low", "category": "cost_probe", "file": "a.py",
             "description": f"batch distinct observation {i} about a drained queue " + "q" * 40}
            for i in range(100)
        ]
        t2 = time.perf_counter()
        findings.batch_add_findings(conn, members)
        t3 = time.perf_counter()
        print(f"\npool≈{pool:5d} rows | add_finding: {per:6.2f} ms/add | "
              f"batch_add(100): {(t3 - t2) * 1000:7.1f} ms total = {(t3 - t2) * 10:5.2f} ms/member")
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)

print()
print("=" * 78)
print("FILE READ + NORMALIZE + HASH — the work an anchor capture adds per observation")
print("=" * 78)


def capture_once(path, line, span=3, ctx=3, cap=1 << 20):
    with open(path, "rb") as fh:
        data = fh.read(cap)
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    lo, hi = max(line - 1, 0), min(line - 1 + span, len(lines))
    seg = [ln.strip() for ln in lines[lo:hi]]
    before = [ln.strip() for ln in lines[max(lo - ctx, 0):lo]]
    after = [ln.strip() for ln in lines[hi:hi + ctx]]
    h = hashlib.sha256(json.dumps(seg, ensure_ascii=False).encode()).hexdigest()[:32]
    ch = hashlib.sha256(json.dumps(before + after, ensure_ascii=False).encode()).hexdigest()[:32]
    return h, ch, len("\n".join(seg))


repo = "/home/faxik/w/codebugs/src/codebugs"
files = []
for base, _, names in os.walk(repo):
    for nm in names:
        if nm.endswith(".py"):
            files.append(os.path.join(base, nm))
files.sort()
sizes = {f: os.path.getsize(f) for f in files}
big = max(sizes, key=sizes.get)

t0 = time.perf_counter()
for f in files:
    capture_once(f, 50)
t1 = time.perf_counter()
warm_ms = (t1 - t0) * 1000
print(f"WARM cache: {len(files)} distinct files, whole-file read + hash: "
      f"{warm_ms:.1f} ms total = {warm_ms / len(files):.3f} ms/file")

subprocess.run(["sync"], capture_output=True)
t0 = time.perf_counter()
for f in files:
    capture_once(f, 50)
t1 = time.perf_counter()
print(f"repeat run (still warm): {(t1 - t0) * 1000:.1f} ms total")

t0 = time.perf_counter()
for _ in range(200):
    capture_once(big, 500)
t1 = time.perf_counter()
print(f"largest file ({sizes[big] / 1024:.0f} KB, {os.path.basename(big)}) x200: "
      f"{(t1 - t0) * 1000 / 200:.3f} ms/capture")

print(f"\nrepo file-size profile (src/codebugs/*.py, n={len(files)}): "
      f"max={max(sizes.values()) / 1024:.0f} KB  "
      f"p50={sorted(sizes.values())[len(sizes) // 2] / 1024:.0f} KB  "
      f"total={sum(sizes.values()) / 1024:.0f} KB")

# how many DISTINCT files does a 100-member batch typically touch?
print("\nA batch touches few distinct paths — measured over the live corpora in pass 1:")
print("  (capture caches per distinct path, so batch cost ~= distinct paths, not members)")
```

### `probe_calibrate2.py`

```python
#!/usr/bin/env python3
"""BT-7 v3 (canon К-5): calibration, linear rewrite of probe_calibrate.py.

Same three questions, but the context-disambiguation pass groups by segment hash
once per file instead of rescanning the file per duplicate (the first draft was
quadratic and did not finish).

Run from the repo root:  PYTHONPATH=src python3 probe_calibrate2.py
"""

import hashlib
import inspect
import json
import os
import re
from collections import Counter, defaultdict

from codebugs import findings

REPOS = ["/home/faxik/w/codebugs", "/home/faxik/w/autosorter"]
SRC_EXT = {".py", ".js", ".ts", ".md", ".sh", ".sql", ".yml", ".yaml", ".toml"}
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".worktrees", ".codebugs", ".mypy_cache"}


def walk(repo, only_py=False):
    out = []
    for base, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for nm in names:
            ext = os.path.splitext(nm)[1]
            if (ext == ".py") if only_py else (ext in SRC_EXT):
                p = os.path.join(base, nm)
                if os.path.isfile(p) and not os.path.islink(p):
                    out.append(p)
    return out


def pct(vals, q):
    v = sorted(vals)
    return v[min(int(len(v) * q), len(v) - 1)]


def norm_line(s):
    s = s.replace("\t", "    ")
    indent = len(s) - len(s.lstrip(" "))
    return f"\x01{indent // 4}\x02{re.sub(r'\\s+', ' ', s.strip())}"


def h(seg):
    return hashlib.sha256(json.dumps(seg, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


print("=" * 78)
print("C1  FILE PROFILE of the two real repositories (sets max_bytes_read / max span)")
print("=" * 78)
allsizes, alllines = [], []
for repo in REPOS:
    files = walk(repo)
    sizes, lines = [], []
    for f in files:
        try:
            sizes.append(os.path.getsize(f))
            with open(f, "rb") as fh:
                lines.append(fh.read().count(b"\n") + 1)
        except OSError:
            pass
    allsizes += sizes
    alllines += lines
    print(f"\n  {repo}  n={len(files)}")
    print(f"    bytes : p50={pct(sizes,.5):>8,}  p95={pct(sizes,.95):>9,}  "
          f"p99={pct(sizes,.99):>9,}  max={max(sizes):>9,}")
    print(f"    lines : p50={pct(lines,.5):>8,}  p95={pct(lines,.95):>9,}  "
          f"p99={pct(lines,.99):>9,}  max={max(lines):>9,}")
print(f"\n  BOTH n={len(allsizes)}  bytes p99={pct(allsizes,.99):,} max={max(allsizes):,}"
      f"  |  lines p99={pct(alllines,.99):,} max={max(alllines):,}")
over = [s for s in allsizes if s > 1 << 20]
print(f"  files over 1 MiB: {len(over)}/{len(allsizes)} ({100*len(over)/len(allsizes):.2f}%)")
over256 = [s for s in allsizes if s > 256 * 1024]
print(f"  files over 256 KiB: {len(over256)}/{len(allsizes)} ({100*len(over256)/len(allsizes):.2f}%)")

print()
print("=" * 78)
print("C2  UNIQUENESS of a normalized k-line segment inside its own file")
print("=" * 78)
py = walk(REPOS[0], only_py=True) + walk(REPOS[1], only_py=True)
print(f"  corpus: {len(py)} python files from both repositories")

CTX = 3
for span in (1, 2, 3, 5):
    tot = dup = dup_ctx = blank = 0
    duplen = []
    for f in py:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                raw = fh.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")
        except OSError:
            continue
        if len(raw) < span + 2 * CTX + 2:
            continue
        norm = [norm_line(x) for x in raw]
        n = len(norm) - span + 1
        seg_h = [h(norm[i:i + span]) for i in range(n)]
        groups = defaultdict(list)
        for i, x in enumerate(seg_h):
            groups[x].append(i)
        ctx_h = {}
        for x, idxs in groups.items():
            if len(idxs) > 1:
                for i in idxs:
                    lo, hi = max(i - CTX, 0), min(i + span + CTX, len(norm))
                    ctx_h[i] = h(norm[lo:i] + norm[i + span:hi])
        for i in range(0, n, 20):
            tot += 1
            body = "".join(x.split("\x02", 1)[1] for x in norm[i:i + span])
            if not body.strip():
                blank += 1
                continue
            idxs = groups[seg_h[i]]
            if len(idxs) > 1:
                dup += 1
                duplen.append(len(body))
                mine = ctx_h[i]
                if sum(1 for j in idxs if ctx_h[j] == mine) > 1:
                    dup_ctx += 1
    print(f"\n  span={span} line(s), sampled positions n={tot:,}")
    print(f"    blank/whitespace-only                   : {blank:,} ({100*blank/max(tot,1):.1f}%)")
    print(f"    NON-UNIQUE inside its own file          : {dup:,} ({100*dup/max(tot,1):.1f}%)")
    print(f"    still non-unique AFTER a {CTX}-line context : {dup_ctx:,} ({100*dup_ctx/max(tot,1):.2f}%)")
    if duplen:
        print(f"    char-length of colliding bodies         : p50={pct(duplen,.5)} "
              f"p90={pct(duplen,.9)} max={max(duplen)}")

print()
print("  Non-uniqueness of a SINGLE normalized line, by its character length:")
buckets = {}
for f in py:
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            raw = fh.read().split("\n")
    except OSError:
        continue
    norm = [norm_line(x) for x in raw]
    counts = Counter(norm)
    for x in norm:
        body = x.split("\x02", 1)[1]
        if not body.strip():
            continue
        b = min(len(body) // 10 * 10, 60)
        t, d = buckets.get(b, (0, 0))
        buckets[b] = (t + 1, d + (1 if counts[x] > 1 else 0))
for b in sorted(buckets):
    t, d = buckets[b]
    lab = f"{b}-{b+9}" if b < 60 else "60+"
    print(f"    body length {lab:>6}: n={t:>8,}  non-unique {100*d/t:5.1f}%")

print()
print("=" * 78)
print("C3  the EXISTING fingerprint's hash spec (the anchor's must not silently diverge)")
print("=" * 78)
for ln in inspect.getsource(findings._derive_fingerprint).splitlines():
    if any(k in ln for k in ("json.dumps", "sha256", "hexdigest", "[:32]", "encode(")):
        print("   ", ln.strip())
```

### Точечная проба уточнения П8 (P-g2)

```bash
PYTHONPATH=src python3 -c "
from codebugs import findings as F
v='fix/cb-95-location-anchor'
desc='capture failed on branch '+v+' while reading the loader'
for lbl,m in (('none',{}),('flat',{'loc_branch':v}),('nested',{'loc':{'branch':v}})):
    print(lbl, F._derive_fingerprint('cat','f.py',desc,m))
"
```
