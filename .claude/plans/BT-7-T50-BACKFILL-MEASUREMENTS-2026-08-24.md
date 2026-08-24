# BT-7 Т-c / Т-50 — арифметика backfill якорей (dry-run, 2026-08-24)

**Ничего не применено.** Все числа ниже — из `recapture_findings(apply=False)`, который по
построению не открывает транзакцию на запись вовсе (форма CB-61; пинуется тестом
`test_a_dry_run_backfill_opens_no_write_transaction_at_all`). `--apply` на живом трекере —
решение владельца, отдельное от посадки этого кода, как это было с ретро-fold категорий.

Скрипт, которым получены числа, вложен целиком в конце файла (К-5в): число без своей
операции — дефект, за который этот пакет уже платил.

---

## Несущий результат, которого я не ожидала: покрытие сегодня — НОЛЬ, а не ~7%

Бриф исходил из того, что триггеры дизайна считаются «по ~7% трекера». Измерено:

| | codebugs | autosorter |
|---|---|---|
| всего строк | **156** | **3355** |
| несут ключ `meta.loc` | **0** | **0** |
| из них координаты / отказ / тумбстоун | 0 / 0 / 0 | 0 / 0 / 0 |
| **безъякорных (популяция backfill)** | **156 (100%)** | **3355 (100%)** |
| `meta` не парсится | 0 | 0 |

**Ни одна строка ни в одном трекере не несёт якоря.** Значит популяция backfill — это весь
корпус, а не хвост, и сегодня backfill является ЕДИНСТВЕННЫМ каналом, способным дать
покрытие вообще.

**Почему так — установлено, и это НЕ дефект захвата.** Резолвер захвата зарегистрирован
(`db.resolver_reserved_meta_keys()` возвращает `['similar_to', 'loc', 'resolver_errors']`),
`loc.py` на main (`d8dabeb`). Но строки, заведённые уже ПОСЛЕ посадки — CB-155 и CB-156 от
2026-08-24 — не несут ни ключа `loc`, ни ключа `resolver_errors`. Резолвер не упал и не был
проглочен: он просто не выполнялся в том процессе, который их заводил. Долгоживущий
MCP-сервер стартовал до посадки `loc.py` и держит модуль-снимок без него. Лечится
перезапуском сервера; кодом здесь чинить нечего. **Эскалация владельцу: пока сервер не
перезапущен, ни одна новая карточка якоря не получит.**

---

## 2. Что backfill дал бы, и что мешает — распределение отказов по токенам Р8

| | codebugs | autosorter |
|---|---|---|
| `would_backfill` (вся популяция) | 156 | 3355 |
| **получили бы РЕАЛЬНЫЕ координаты** | **22** | **272** |
| записали бы отказной объект | 134 | 3083 |
| `unreadable_meta` (посчитаны, не тронуты) | 0 | 0 |

**Распределение отказов по токенам Р8 — это и есть ответ на «что именно мешает покрытию»:**

| токен Р8 | codebugs | доля популяции | autosorter | доля популяции |
|---|---|---|---|---|
| `no_grammar` — строка не говорит, где смотреть | 121 | 77.6% | 2667 | 79.5% |
| `no_commit` — нет `reported_at_commit` | 10 | 6.4% | 372 | 11.1% |
| `too_short` | 2 | 1.3% | 17 | 0.5% |
| `no_matching_site` | 1 | 0.6% | 14 | 0.4% |
| `path_absent_at_commit` | 0 | — | 10 | 0.3% |
| `out_of_range` | 0 | — | 2 | 0.1% |
| `commit_unreachable` | 0 | — | 1 | 0.0% |

**Читать так:** потолок ставит не механизм и не среда, а ФОРМА ЗАВЕДЕНИЯ — 77.6% / 79.5%
строк не называют пролёта. Это ровно тот потолок, который дизайн назвал своим в §1б, и
backfill его не двигает и двигать не может. Второй по величине — `no_commit` (6.4% / 11.1%),
и он-то механизируем: это карточки без `reported_at_commit`, то есть ровно та дыра, которую
закрыл CB-144 для CLI. Ни `shallow`, ни бюджет в отказах не появились ни разу.

---

## 3. Покрытие ДО / ПОСЛЕ, в единицах §1б

Единицы §1б: числитель — строки, годные ядру («пролёт + разрешимый коммит + файл в нём»),
знаменатель — ВСЕ строки трекера. Моя мера — строки, несущие якорь С КООРДИНАТАМИ, к тем же
всем строкам. Это то же множество с другого конца: §1б его предсказывает, я его считаю.

| | codebugs | autosorter |
|---|---|---|
| **ДО** | 0 / 156 = **0.00%** | 0 / 3355 = **0.00%** |
| **ПОСЛЕ** | 22 / 156 = **14.10%** | 272 / 3355 = **8.11%** |
| прирост | +22 строки, +14.10 п.п. | +272 строки, +8.11 п.п. |
| §1б v6 «годна ядру» | 37 / 129 = 28.7% | 314 / 3300 = 9.5% |

**ПОСЛЕ вычислено, а не измерено, и это разные утверждения.** ПОСЛЕ = уже несущие координаты
(0) + те, кому dry-run обещает координаты. Записи не было нигде.

### Единицы совпали; ЧИСЛА §1б на codebugs НЕ ВОСПРОИЗВЕЛИСЬ — говорю вслух, не подгоняю

- **autosorter воспроизвёлся хорошо**: 8.11% против 9.5% (корпус вырос 3300→3355), и
  `no_grammar` 79.5% против 79.2%. Расхождение объяснимо ростом корпуса.
- **codebugs не воспроизвёлся**: 14.10% против 28.7%, вдвое. Источник расхождения найден и он
  НЕ в моём коде — не воспроизводится сама ВХОДНАЯ строка таблицы §1б. Дизайн: «несёт
  разбираемый пролёт — 47 (36.4%) из 129». Пересчитано `loc.parse_sites` сегодня:
  **35 из 156 = 22.4%**, а на ОДНИХ СТАРЕЙШИХ 129 строках — **35 из 129 = 27.1%**, не 47.
  Из 27 строк, заведённых после снимка 2026-08-22, пролёт не несёт **ни одна**.
- Оговорка к этому сравнению: «старейшие 129 строк» — не обязательно тот же самый набор, что
  держал CSV-снимок `codebugs-live-2026-08-22.csv` (оба трекера мутабельны, дизайн это сам
  оговаривает). Поэтому я утверждаю только то, что измерила: **сегодня предикат §1б даёт 35, а
  не 47**, и разницу в 12 строк этот прогон не объясняет.
- Остаток 27.1% → 14.10% объясняется полностью: 10 строк `no_commit`, 2 `too_short`, 1
  `no_matching_site` — то есть §1б считал «годна ядру» предикатом более щедрым, чем реальный
  захват (`too_short` и `no_matching_site` он не учитывал вовсе).

**Вывод для владельца одной строкой:** backfill даёт 22 карточки codebugs и 272 карточки
autosorter с настоящими координатами там, где сегодня их ноль; выше 14% / 8% его не поднять
ничем, кроме изменения того, КАК заводятся карточки.

---

## 4. Скрипт (К-5в) — им получены все числа выше

Прогон: `CODEBUGS_ROOT= uv run --extra dev python <этот скрипт>` из worktree ветки.
`.py` в `.claude/plans/` не пускает `pre-commit` на main, поэтому скрипт вложен сюда
(прецедент `BT-7-MEASUREMENTS-FLAGS-2026-08-23.md`).

```python
"""T-50 S6 arithmetic. DRY RUN on the live trackers -- no write transaction.

Nothing is written anywhere. `recapture_findings(apply=False)` opens no write
transaction AT ALL (that is the CB-61 dry-run form, and a test pins it), so the
pass is a read even on a live tracker. Rows are read through the public seam
`findings.anchor_candidates`, never by hand -- this script issues no SQL.

The AFTER column is therefore COMPUTED from the dry run rather than measured on
a mutated copy: AFTER = rows that already carry coordinates + rows the dry run
says would gain them. Said plainly, because a computed number and a measured one
are not the same claim.
"""
import collections, json, sys

sys.path.insert(0, "src")
from codebugs import db, findings, loc  # noqa: E402


def anchor_state(meta_json):
    try:
        m = json.loads(meta_json or "{}")
    except (TypeError, ValueError):
        return "unreadable"
    if not isinstance(m, dict):
        return "unreadable"
    if "loc" not in m:
        return "absent"
    v = m["loc"]
    if v is None:
        return "tombstone"
    if isinstance(v, dict) and "skipped" in v:
        return "refusal"
    return "coordinates"


def report(name, root, project_dir):
    print(f"\n{'=' * 74}\n{name}\n  tracker root:     {root}\n"
          f"  captured against: {project_dir}\n{'=' * 74}")
    db.set_tracker_root(root)
    conn = db.connect()

    rows = findings.anchor_candidates(conn, status=None, limit=1000000)
    before = collections.Counter(anchor_state(r["meta_json"]) for r in rows)
    total = len(rows)
    keyed = total - before["absent"] - before["unreadable"]
    print("\n1. POPULATION (every row, status=all)")
    print(f"   total rows                          {total}")
    print(f"   carry a `loc` key                   {keyed}")
    print(f"     .. real coordinates               {before['coordinates']}")
    print(f"     .. a refusal object               {before['refusal']}")
    print(f"     .. a `loc: null` tombstone        {before['tombstone']}")
    print(f"   carry NO `loc` key  (BACKFILL POP)  {before['absent']}")
    print(f"   `meta` does not parse               {before['unreadable']}")

    dry = loc.recapture_findings(conn, status="all", project_dir=project_dir,
                                 include_unanchored=True, apply=False, limit=1000000)
    bf = [r for r in dry["results"] if r["outcome"] == "would_backfill"]
    gain = [r for r in bf if r["reason"] is None]
    refuse = collections.Counter(r["reason"] for r in bf if r["reason"] is not None)
    print("\n2. THE BACKFILL POPULATION, dry run")
    print(f"   would_backfill                      {len(bf)}")
    print(f"     .. would GAIN real coordinates    {len(gain)}")
    print(f"     .. would record a refusal         {sum(refuse.values())}")
    print(f"   unreadable_meta (counted, untouched){dry['summary']['unreadable_meta']:>4}")
    print("\n   REFUSAL DISTRIBUTION BY P8 TOKEN -- what actually caps coverage:")
    for tok, n in refuse.most_common():
        print(f"     {tok:<26} {n:>6}   {100 * n / max(1, len(bf)):5.1f}% of the backfill pop")
    print(f"\n   (the already-anchored rows, for contrast: would_update="
          f"{dry['summary']['would_update']}, unchanged={dry['summary']['unchanged']}, "
          f"kept={dry['summary']['kept']}, tombstoned={dry['summary']['tombstoned']}, "
          f"stale={dry['summary']['stale']})")

    after = before["coordinates"] + len(gain)
    print("\n3. COVERAGE BEFORE / AFTER  (S1b units: real coordinates / all rows)")
    print(f"   BEFORE  {before['coordinates']:>6} / {total} = "
          f"{100 * before['coordinates'] / max(1, total):5.2f}%")
    print(f"   AFTER   {after:>6} / {total} = {100 * after / max(1, total):5.2f}%")
    print(f"   gained  {len(gain):>6} rows = "
          f"{100 * len(gain) / max(1, total):5.2f} percentage points")
    conn.close()


if __name__ == "__main__":
    report("codebugs (this tracker)", "/home/faxik/w/codebugs", "/home/faxik/w/codebugs")
    report("autosorter", "/home/faxik/w/autosorter", "/home/faxik/w/autosorter")
```

### Проверочный скрипт к разделам 1 и 3 (почему ноль; предикат §1б своими руками)

```python
import sys, json, collections
sys.path.insert(0, "src")
from codebugs import db, findings, loc

db.set_tracker_root("/home/faxik/w/codebugs")
conn = db.connect()
rows = findings.anchor_candidates(conn, status=None, limit=1000000)
rows = sorted(rows, key=lambda r: (r["created_at"] or "", r["id"]))
print("newest 5 rows (created_at, id, has loc key):")
for r in rows[-5:]:
    m = json.loads(r["meta_json"] or "{}")
    print("  ", r["created_at"], r["id"], "loc" in (m if isinstance(m, dict) else {}))
print("\nis the capture resolver registered in THIS process?",
      [n for n in db.resolver_reserved_meta_keys()])
# S1b predicate, with its own operation: how many rows carry a parseable span?
def parses(r):
    m = json.loads(r["meta_json"] or "{}")
    return bool(loc.parse_sites(m if isinstance(m, dict) else {}))
n_all = sum(parses(r) for r in rows)
print(f"\nS1b 'carries a parseable span': {n_all}/{len(rows)} = {100*n_all/len(rows):.1f}%")
first129 = rows[:129]
n129 = sum(parses(r) for r in first129)
print(f"  restricted to the OLDEST 129 rows (the design's N): {n129}/129 = {100*n129/129:.1f}%"
      f"   [design v6 says 47 = 36.4%]")
newer = rows[129:]
print(f"  the {len(newer)} rows filed since: {sum(parses(r) for r in newer)} carry a span")
```

### Вывод обоих прогонов, дословно

```

==========================================================================
codebugs (this tracker)
  tracker root:     /home/faxik/w/codebugs
  captured against: /home/faxik/w/codebugs
==========================================================================

1. POPULATION (every row, status=all)
   total rows                          156
   carry a `loc` key                   0
     .. real coordinates               0
     .. a refusal object               0
     .. a `loc: null` tombstone        0
   carry NO `loc` key  (BACKFILL POP)  156
   `meta` does not parse               0

2. THE BACKFILL POPULATION, dry run
   would_backfill                      156
     .. would GAIN real coordinates    22
     .. would record a refusal         134
   unreadable_meta (counted, untouched)   0

   REFUSAL DISTRIBUTION BY P8 TOKEN -- what actually caps coverage:
     no_grammar                    121    77.6% of the backfill pop
     no_commit                      10     6.4% of the backfill pop
     too_short                       2     1.3% of the backfill pop
     no_matching_site                1     0.6% of the backfill pop

   (the already-anchored rows, for contrast: would_update=0, unchanged=0, kept=0, tombstoned=0, stale=0)

3. COVERAGE BEFORE / AFTER  (S1b units: real coordinates / all rows)
   BEFORE       0 / 156 =  0.00%
   AFTER       22 / 156 = 14.10%
   gained      22 rows = 14.10 percentage points

==========================================================================
autosorter
  tracker root:     /home/faxik/w/autosorter
  captured against: /home/faxik/w/autosorter
==========================================================================

1. POPULATION (every row, status=all)
   total rows                          3355
   carry a `loc` key                   0
     .. real coordinates               0
     .. a refusal object               0
     .. a `loc: null` tombstone        0
   carry NO `loc` key  (BACKFILL POP)  3355
   `meta` does not parse               0

2. THE BACKFILL POPULATION, dry run
   would_backfill                      3355
     .. would GAIN real coordinates    272
     .. would record a refusal         3083
   unreadable_meta (counted, untouched)   0

   REFUSAL DISTRIBUTION BY P8 TOKEN -- what actually caps coverage:
     no_grammar                   2667    79.5% of the backfill pop
     no_commit                     372    11.1% of the backfill pop
     too_short                      17     0.5% of the backfill pop
     no_matching_site               14     0.4% of the backfill pop
     path_absent_at_commit          10     0.3% of the backfill pop
     out_of_range                    2     0.1% of the backfill pop
     commit_unreachable              1     0.0% of the backfill pop

   (the already-anchored rows, for contrast: would_update=0, unchanged=0, kept=0, tombstoned=0, stale=0)

3. COVERAGE BEFORE / AFTER  (S1b units: real coordinates / all rows)
   BEFORE       0 / 3355 =  0.00%
   AFTER      272 / 3355 =  8.11%
   gained     272 rows =  8.11 percentage points
```
