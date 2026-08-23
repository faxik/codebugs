# BT-7 — ПЕРЕМЕР ВЫБОРА ФЛАГА `git blame --reverse`, 2026-08-23

**Кто и когда мерил:** держатель направления DIR-2, уровень (2), сессия 7, 2026-08-23.
**Зачем:** несущее число, по которому BT-7 v6 выбрала флаг `-C -C`, не имело при себе своей
ОПЕРАЦИИ — то есть нарушало канон К-5в, принятый по предложению этого же направления.
Сохранённый `revblame_measure.py` гоняет фиксированный `-M -C` и матрицы флагов не строит,
а строка «`-C` ловит 1, `-C -C` ловит 2» пришла поправкой узкого числового аудита без скрипта.
Спор запустил измерительный субагент юнита Т-43, доложивший (структурно), что в реверс-режиме
`-C -C` вообще не может отличаться от `-C`.

**Итог одной строкой: обе стороны спора оказались неправы, и правильный флаг — `-C`.**

---

## 1. ТАБЛИЦА ЧИСЕЛ — каждая цифра со своим ПРЕДИКАТОМ (К-5б)

**Популяция и её отбор.** Корпус `/home/faxik/.cache/bt7-corpus-2026-08-22/autosorter-live-2026-08-22.csv`
(sha256 записан в `hashes.txt` рядом), 3300 логических записей CSV. Репозиторий
`~/workspace-private/autosorter`, HEAD на момент прогона `2418031` — **не** `7f7d80b4c`,
названный в задании: голова сдвинулась за четыре минуты между чтением и прогоном, потому что
репозиторий живой. Это записано, а не сглажено: одна из найденных ниже атрибуций указывает
ровно на этот новый HEAD.

| число | предикат | знаменатель |
|---|---|---|
| **134** | годная строка: есть `reported_at_commit`, есть `file`, из `meta.lines` разобран хотя бы один пролёт, **и коммит — предок HEAD** | 3300 записей корпуса |
| 1151 | отсеяно: нет `reported_at_commit` | 3300 |
| 1935 | отсеяно: нет пригодного `meta.lines` | 3300 |
| 6 | отсеяно: `lines` есть, но ни один пролёт не разобрался | 3300 |
| **74** | отсеяно ГЕЙТОМ ПРЕДКА: коммит не предок HEAD | 3300 |
| 87 | пролётов отброшено как ссылающиеся на ЧУЖОЙ файл (`bench.py:771`) — счётчик пролётов, не строк | — |
| 7 | годных строк, где blame отказал `fatal: no such path <file> in <commit>` — **одинаково у всех трёх наборов флагов** | 134 |

**Кандидаты `moved_file`** (в выводе blame запись `filename` ≠ записанному `file`):

| флаги | кандидатов `moved_file` | из них ИСТИННЫХ |
|---|---|---|
| `-C` | **0** | 0 |
| `-C -C` | **1** | **0 — единственная ложная** |
| `-M -C` | **0** | 0 |

**Решающая цифра спора — различаются ли `-C` и `-C -C`:**

| | значение |
|---|---|
| строк, где `-C` и `-C -C` дали ХОТЬ ЧТО-ТО разное (rc, stdout побайтно) | **1 из 134** |
| строк, где вывод побайтно идентичен | **133 из 134** |

**Цена** (медиана одного вызова, n=134 на набор): `-C` — 29.57 мс, `-C -C` — 31.38 мс,
`-M -C` — 29.89 мс. Всего 402 вызова, 17.2 с стенных часов.

---

## 2. Что именно нашлось — единственное расхождение, и оно ложное

- **id:** CB-1596
- **записанный путь:** `src/autosorter/mcp/server.py`
- **`reported_at_commit`:** `8819d0c25fae77858bdd494e8cbf414643955ecd`
- **пролёт после клиппинга до `MAX_ANCHOR_LINES=5`:** 649–649 (ОДНА строка)
- **`-C`** → `src/autosorter/mcp/server.py`, sha `0b13c36…` (коммит правки того же файла)
- **`-C -C`** → `docs/superpowers/plans/2026-06-02-watcher-autostart-and-100pct-setup.md`,
  sha `2418031…` — **это сам HEAD на момент прогона**
- **`-M -C`** → то же, что `-C`

**Механизм.** Строка кода `_auto_start_watcher_background(server, resolve_data_dir(data_dir))`
дословно процитирована как пример в markdown-плане. Дополнительный скоуп второго `-C` —
«копии из других файлов в коммите СОЗДАНИЯ файла» — нашёл эту цитату и приписал якорю
документ. **Это отменяет и структурное утверждение измерительного субагента**, будто в
реверс-режиме второй `-C` не может ничего добавить: может, и добавляет — ровно ложное.

**Производитель систематический, и это наш собственный процесс.** Каскад непрерывно пишет
плановые заметки, цитирующие код вербатим. Каждая такая цитата — кандидат увести якорь в
документ, и чем активнее направление документирует, тем больше таких кандидатов.

**Названный предел, который эта находка вскрыла и который флагом НЕ закрывается.**
Обязательная сверка (Р5, шаг 2) этот случай **не ловит**: цитата совпадает с сохранённым
текстом побайтно, сверка проходит, и уверенно неверный ответ уходит потребителю как
`moved_file`. Сверка отсекает грубую ошибку и **не отличает КОПИЮ от ПЕРЕЕЗДА**.

---

## 3. Ручная проверка, независимая от парсера

Три вызова в шелле дали те же три разных ответа, что и скрипт:

```
git -c core.quotePath=false blame --reverse -p -C    -L 649,649 8819d0c2…..HEAD -- src/autosorter/mcp/server.py
git -c core.quotePath=false blame --reverse -p -C -C -L 649,649 8819d0c2…..HEAD -- src/autosorter/mcp/server.py
git -c core.quotePath=false blame --reverse -p -M -C -L 649,649 8819d0c2…..HEAD -- src/autosorter/mcp/server.py
```

---

## 4. Честные оговорки — против собственного результата

- **Грамматика `meta.lines` — свободный текст, и парсер эвристический.** Он берёт первый
  пригодный пролёт на строку и сопоставляет чужой файл по `endswith`/`basename`. Он мог
  пропустить пригодный пролёт (6 случаев «no_usable_span») или в пограничном случае засчитать
  чужой пролёт как свой. **Это влияет на ЗНАМЕНАТЕЛЬ (134), но не на найденное расхождение** —
  оно проверено вручную независимо от парсера.
- **Число v6 не просто «уточнено», оно НЕ ВОСПРОИЗВЕЛОСЬ ни одной половиной.** `-C`→1
  не воспроизвелось (стало 0), `-C -C`→2 не воспроизвелось (стало 1, и та ложная). Причина
  расхождения с прошлым прогоном не установлена, потому что **скрипта того прогона не
  существует** — это ровно та цена, которую К-5в назначен предотвращать.
- **Отдельный кандидат в проверку, не сделанную здесь:** узкий числовой аудит v5→v6 называл
  CB-3055 единственным подтверждённым `moved_file` на живой истории (переезд в
  `ignore_rules.py`). В этом прогоне кандидатов `moved_file` у `-C` ноль, значит CB-3055 либо
  не попал в 134 годные строки (пролёт не разобрался, или коммит не предок), либо переезд
  больше не виден. Не расследовано — назвал, чтобы не потерялось.

---

## 5. Решение, принятое по этому замеру

**Флаг канала A — `-C` одиночный.** Это правка БУКВЫ ратифицированного v6 под её же СМЫСЛ,
сделанная держателем (2): владелец ратифицировал конструкцию (reverse-blame-ядро, хранимый
текст, обязательную сверку, `moved_file` как видимый статус), а флаг был внутренним выбором
дизайна, обоснованным числом; число не воспроизвелось, смысл не изменился, и `-C` служит ему
строго лучше — ноль ложных срабатываний при той же способности ловить split.
`-M -C` не рассматривается: контролируемый опыт §6 замеров reverse-blame показал, что он
split ТЕРЯЕТ, который `-C` ловит. `MAX_ANCHOR_LINES = 5` НЕ отменяется — он держит именно
эту способность.

Правки в `BT-7-location-anchor.md`: команда канала A в Р5, таблица чисел §1б, таблица правок
v5→v6 и абзац обоснования флага.

---

## 6. ОПЕРАЦИЯ ЗАМЕРА (К-5в) — скрипт целиком, как опровержимая улика

Запуск: `python3 <скрипт> <corpus.csv> <repo_root>`; лежал при прогоне вне репозитория,
sha256 файла на момент прогона — `7cc6064d928b7d68c593cdc404b0f29a5e571bfbb3eebca880c5d064bc7e3df1`.
Текст вкладывается сюда, а не рядом файлом, потому что pre-commit на `main` по построению
отказывает всему, кроме `.claude/plans/*.md`, — улика обязана лежать там, где её прочтут.

```python
#!/usr/bin/env python3
"""
Измерение: различаются ли git blame --reverse -C и -C -C на живой истории autosorter.
Read-only: только `git -C <repo> ...` читающие команды. Ничего не пишет в репозиторий.
"""
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time

REPO = "/home/faxik/workspace-private/autosorter"
CORPUS = "/home/faxik/.cache/bt7-corpus-2026-08-22/autosorter-live-2026-08-22.csv"
MAX_ANCHOR_LINES = 5
LIMIT_VALID_ROWS = int(os.environ.get("REVBLAME_LIMIT", "400"))
BLAME_TIMEOUT = 60

FLAG_SETS = {
    "-C": ["-C"],
    "-C -C": ["-C", "-C"],
    "-M -C": ["-M", "-C"],
}

csv.field_size_limit(10_000_000)


def run(cmd, timeout=30):
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
        dt = time.perf_counter() - t0
        return p.returncode, p.stdout, p.stderr, dt
    except subprocess.TimeoutExpired:
        dt = time.perf_counter() - t0
        return -999, "", "TIMEOUT", dt


def git(args, timeout=30):
    return run(["git", "-C", REPO] + args, timeout=timeout)


# ---------- meta.lines free-grammar parsing ----------

NUM_RE = r"~?(\d+)(?:\s*-\s*(\d+))?"
FULL_PATH_RE = re.compile(r"^([\w./\-]+\.\w+)\s*:\s*" + NUM_RE)
COLON_ONLY_RE = re.compile(r"^:\s*" + NUM_RE)
BARE_NUM_RE = re.compile(r"^" + NUM_RE)


def normalize_lines_value(v):
    """Turn meta['lines'] (str | list | dict) into one grammar string."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    if isinstance(v, dict):
        return ",".join(str(x) for x in v.values())
    return None


def extract_spans(lines_value, main_file):
    """
    Returns (spans, other_file_skipped_count)
    spans: list of (path_or_None, start:int, end:int) in first-seen order.
    path_or_None means: belongs to the CSV's own `file` column (no path token seen).
    """
    s = normalize_lines_value(lines_value)
    if not s:
        return [], 0

    spans = []
    other_skipped = 0
    for top_seg in s.split(";"):
        current_path = None
        for part in top_seg.split(","):
            part = part.strip()
            if not part:
                continue
            m = FULL_PATH_RE.match(part)
            if m:
                current_path = m.group(1)
                start = int(m.group(2))
                end = int(m.group(3)) if m.group(3) else start
                spans.append((current_path, start, end))
                continue
            m = COLON_ONLY_RE.match(part)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else start
                spans.append((current_path, start, end))
                continue
            m = BARE_NUM_RE.match(part)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else start
                spans.append((current_path, start, end))
                continue
            # dud fragment (e.g. "core/db/document_search.py order_by created_at desc")
            continue

    # classify against main_file
    usable = []
    for path, start, end in spans:
        if path is None:
            usable.append((start, end))
        else:
            base_path = os.path.basename(path)
            base_main = os.path.basename(main_file)
            if main_file.endswith(path) or base_path == base_main:
                usable.append((start, end))
            else:
                other_skipped += 1
    return usable, other_skipped


def clip_span(start, end):
    if end < start:
        start, end = end, start
    if end - start + 1 > MAX_ANCHOR_LINES:
        end = start + MAX_ANCHOR_LINES - 1
    return start, end


# ---------- porcelain parsing ----------

SHA_HEAD_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


def parse_porcelain(text):
    """Return list of (sha, filename) per output source line, in order."""
    sha = None
    filename = None
    out = []
    for line in text.split("\n"):
        m = SHA_HEAD_RE.match(line)
        if m:
            sha = m.group(1)
            continue
        if line.startswith("filename "):
            filename = line[len("filename "):]
            continue
        if line.startswith("\t"):
            out.append((sha, filename))
    return out


def main():
    rows_total = 0
    ctr = {
        "no_commit": 0,
        "no_file": 0,
        "no_lines_field": 0,
        "unparseable_meta": 0,
        "no_usable_span": 0,
        "bad_commit_ref": 0,
        "not_ancestor": 0,
        "other_file_spans_skipped": 0,
        "valid": 0,
    }

    valid_rows = []  # (id, file, commit_full, start, end)

    with open(CORPUS, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_total += 1
            if len(valid_rows) >= LIMIT_VALID_ROWS:
                # keep counting gate-failure stats for the whole corpus? No -
                # spec says "ограничься первыми 400 годными строками" -> stop
                # scanning once we have 400 valid ones.
                break

            fid = row.get("id", "")
            file_col = (row.get("file") or "").strip()
            commit_col = (row.get("reported_at_commit") or "").strip()
            meta_raw = row.get("meta") or ""

            if not commit_col:
                ctr["no_commit"] += 1
                continue
            if not file_col:
                ctr["no_file"] += 1
                continue

            if not meta_raw:
                ctr["no_lines_field"] += 1
                continue
            try:
                meta = json.loads(meta_raw)
            except Exception:
                ctr["unparseable_meta"] += 1
                continue
            if not isinstance(meta, dict) or "lines" not in meta or meta["lines"] in (None, "", []):
                ctr["no_lines_field"] += 1
                continue

            spans, other_skipped = extract_spans(meta["lines"], file_col)
            ctr["other_file_spans_skipped"] += other_skipped
            if not spans:
                ctr["no_usable_span"] += 1
                continue

            # resolve commit ref
            rc, out, err, _ = git(["rev-parse", "--verify", commit_col + "^{commit}"], timeout=15)
            if rc != 0:
                ctr["bad_commit_ref"] += 1
                continue
            full_commit = out.strip()

            rc, out, err, _ = git(["merge-base", "--is-ancestor", full_commit, "HEAD"], timeout=15)
            if rc != 0:
                ctr["not_ancestor"] += 1
                continue

            start, end = spans[0]
            start, end = clip_span(start, end)

            valid_rows.append({
                "id": fid,
                "file": file_col,
                "commit": full_commit,
                "commit_short": commit_col,
                "start": start,
                "end": end,
            })
            ctr["valid"] += 1

    print(f"# scanned corpus rows (until {LIMIT_VALID_ROWS} valid found or EOF): {rows_total}")
    print(f"# gate counters: {json.dumps(ctr, indent=2)}")
    print(f"# valid rows collected: {len(valid_rows)}")
    sys.stdout.flush()

    # ---- run blame for each flag set ----
    timings = {name: [] for name in FLAG_SETS}
    moved_file_counts = {name: 0 for name in FLAG_SETS}
    error_counts = {name: 0 for name in FLAG_SETS}
    empty_output_counts = {name: 0 for name in FLAG_SETS}

    per_row_results = {}  # id -> {flagname: {"rc":, "stdout":, "lines":[(sha,filename)]}}

    for i, r in enumerate(valid_rows):
        rev_range = f"{r['commit']}..HEAD"
        line_arg = f"{r['start']},{r['end']}"
        row_out = {}
        for name, flags in FLAG_SETS.items():
            cmd = ["git", "-C", REPO, "-c", "core.quotePath=false", "blame",
                   "--reverse", "-p"] + flags + ["-L", line_arg, rev_range, "--", r["file"]]
            rc, out, err, dt = run(cmd, timeout=BLAME_TIMEOUT)
            timings[name].append(dt)
            if rc != 0:
                error_counts[name] += 1
            elif not out.strip():
                empty_output_counts[name] += 1
            parsed = parse_porcelain(out) if rc == 0 else []
            filenames = {fn for (_, fn) in parsed if fn is not None}
            if filenames and not any(fn == r["file"] for fn in filenames):
                moved_file_counts[name] += 1
            row_out[name] = {"rc": rc, "stdout": out, "stderr": err, "parsed": parsed, "filenames": filenames}
        per_row_results[r["id"]] = row_out
        if (i + 1) % 50 == 0:
            print(f"# progress: {i+1}/{len(valid_rows)} rows done", flush=True)

    # ---- compare -C vs -C -C ----
    diffs = []
    for r in valid_rows:
        fid = r["id"]
        a = per_row_results[fid]["-C"]
        b = per_row_results[fid]["-C -C"]
        differs = False
        reasons = []
        if a["rc"] != b["rc"]:
            differs = True
            reasons.append(f"returncode {a['rc']} vs {b['rc']}")
        if a["stdout"] != b["stdout"]:
            differs = True
            if a["filenames"] != b["filenames"]:
                reasons.append(f"filenames {a['filenames']} vs {b['filenames']}")
            if [s for s, _ in a["parsed"]] != [s for s, _ in b["parsed"]]:
                reasons.append("sha sequence differs")
            if len(a["parsed"]) != len(b["parsed"]):
                reasons.append(f"line count {len(a['parsed'])} vs {len(b['parsed'])}")
            if not reasons:
                reasons.append("stdout differs (byte-level, cause unclassified above)")
        if differs:
            diffs.append({
                "id": fid,
                "file": r["file"],
                "commit": r["commit"],
                "span": f"{r['start']}-{r['end']}",
                "reasons": reasons,
                "-C_filenames": sorted(a["filenames"]),
                "-C -C_filenames": sorted(b["filenames"]),
            })

    print("\n# ===== RESULTS =====")
    print(f"valid_rows_used = {len(valid_rows)} (denominator; corpus scan capped at first {LIMIT_VALID_ROWS} valid rows or EOF)")
    print(f"gate_counters = {json.dumps(ctr, indent=2)}")
    print(f"other_file_spans_skipped (separate counter, spans not rows) = {ctr['other_file_spans_skipped']}")
    print()
    for name in FLAG_SETS:
        print(f"[{name}] errors(rc!=0)={error_counts[name]} empty_output={empty_output_counts[name]} "
              f"moved_file_candidates={moved_file_counts[name]} "
              f"median_ms={statistics.median(timings[name])*1000:.2f} "
              f"n_calls={len(timings[name])} "
              f"min_ms={min(timings[name])*1000:.2f} max_ms={max(timings[name])*1000:.2f}")
    print()
    print(f"DIFF COUNT (-C vs -C -C): {len(diffs)}")
    for d in diffs:
        print(json.dumps(d, indent=2))

    if not diffs:
        print("\n# ZERO differences between -C and -C -C on this live-history sample.")


if __name__ == "__main__":
    main()
```
