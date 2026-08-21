# UNIT Т-17 — CB-21 (parity-гейт + декларация остаточных ячеек) + CB-122 (вакуозный тест)

Тонкий механический бриф (§12). Уровень (3) → (4). Ветка `fix/cb-21-cb-122-parity-gate`,
база `9c3b6b7`. Направление DIR-2 (findings identity). Хендофф (2)→(3):
`.claude/plans/L3-BRIEF-DIR-2-T17-parity-gate-2026-08-21.md`.

## unit

Два экземпляра одной болезни — правило, которое никто не проверяет механически.
CB-21: матрица мутабельности колонок не объявлена нигде, поэтому каждая карта закрывает
одну ячейку руками, и три независимых прохода по одной функции нашли три РАЗНЫЕ
пропущенные колонки. CB-122: тест декларирует проверку не-строковой категории, но
вставляет число в TEXT-колонку и получает строку — политика skip-не-raise не пришпилена.

## intent

Превратить прозу в ГЕЙТ. Не «сделать колонки мутабельными» — а сделать так, чтобы любая
колонка обязана была быть ОБЪЯВЛЕННОЙ (мутабельной либо неизменяемой с причиной), и чтобы
следующая новая колонка падала в тесте, а не обнаруживалась четвёртым проходом по осмотру.

## files

- НОВЫЙ `tests/test_update_parity.py` — parity-гейт (A).
- `src/codebugs/findings.py` — ТОЛЬКО докстринг `update_finding` (B). Ноль правок
  исполняемых путей записи.
- `tests/test_category_gate.py` — `TestLegacyWeirdRows` (C).
- `CLAUDE.md` — один буллет про CB-21, только если карта закрывается (расширение границ
  брифа, объявлено в возврате).
- `reqs.py` — ЧИТАЕТСЯ, НЕ ПРАВИТСЯ (точка сериализации, не заявлена ни за одним
  направлением).

## acceptance

- (A) гейт зелен на текущем дереве и падает на искусственно «забытой» колонке
  (мутационная проба — обе стороны, во ВРЕМЕННОМ worktree).
- (B) `description`/`category`/`file`/`source` объявлены с причинами; дифф `src/` —
  только докстринг.
- (C) `TestLegacyWeirdRows` использует `CAST(... AS BLOB)`, утверждает, что фикстура
  не-строковая, падает при снятии политики skip (`isinstance(row["category"], str)` в
  `_existing_categories`) и проходит на текущем коде.
- Сюита зелёная, `ruff check src/ tests/` чист.

## preflight (§4, вердикты уровня (3), добыты против ПРИЗЕМЛЁННОГО кода 2026-08-21)

1. **Цитаты карт существуют и говорят заявленное — ДЕРЖИТСЯ.**
   - `update_finding` принимает `status, severity, notes, append_note, tags, meta_update,
     reported_at_ref` — колоночная проекция ровно та, что называет CB-21.
   - `update_requirement` принимает `description` — асимметрия CB-17, названная картой,
     реальна.
   - `source` не встречается ни в одной из двух сигнатур — коррекция CB-21 (Codex-аудит)
     держится.
   - `_cmd_update` форвардит `status, severity, notes, append_note` — дословно как в карте.
   - `_cmd_reqs_update` форвардит `status, description, priority, test_coverage, notes` —
     дословно как в карте.
   - CB-122: `TestLegacyWeirdRows` вставляет `'CB-666', 'low', 5, 'f.py'` — целое `5` в
     колонку `category TEXT NOT NULL`. Исправная фикстура существует:
     `tests/test_category_fold.py::TestNonStringCategoryIsSkipped` (`CAST(7 AS BLOB)` +
     `assert not isinstance(...)`).
2. **Предпосылки против СХЕМЫ — ДЕРЖАТСЯ.** `findings` = 16 колонок (`id, severity,
   category, file, status, description, source, tags, meta, reported_at_commit,
   reported_at_ref, created_at, updated_at, fingerprint, occurrence_count,
   last_seen_at`); `requirements` = 12 (`id, section, description, priority, status,
   source, test_coverage, tags, meta, embedding, created_at, updated_at`). `category
   TEXT NOT NULL` → TEXT-аффинность → вставленное целое возвращается как `'5'`, что и
   есть механизм CB-122.
3. **Названный файл — место фикса.** Да: гейт — новый тест-файл (карта прямо просит
   «~30 строк, один новый файл»); декларация — докстринг `update_finding`; вакуозный тест
   — `tests/test_category_gate.py`.
4. **Пересечений с in-flight нет.** `git worktree list` на момент диспетчеризации — только
   main; параллельный Т-16 (CB-123) работает в регионах `query_findings`/ORDER BY, наш
   регион — докстринг `update_finding` и тесты. `reqs.py` не правится вовсе.
5. **Не залежался** — бриф выдан и диспетчеризован в тот же день.

## факт приёмки

_(заполняется уровнем (3) после приземления)_
