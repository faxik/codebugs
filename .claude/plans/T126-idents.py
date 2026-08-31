#!/usr/bin/env python3
"""Т-126, §5 п. 3 и четвёртый (отчётный) гейт: сравнение МНОЖЕСТВ до и после.

Запуск:
    python3 .claude/plans/T126-idents.py snapshot <файл> <куда.json>
    python3 .claude/plans/T126-idents.py diff <было.json> <стало.json>

Два множества, намеренно разные по режиму:

*   **Идентификаторы карт** — `CB-N`, `T-N`, `ARCH-00N`, `BT-N`. Пропажа любого есть
    ОТКАЗ (§3 п. 4 брифа: «любой из идентификаторов §2 п. 5» резать нельзя).
    Семейство `T-N` **латинское**: кириллических `Т-N` в документе ноль, и оракул,
    написанный кириллицей, сравнивал бы пустое множество с пустым и всегда зеленел.
    `CB-9001` — вымышленный пример внутри правила CB-51, а не карта; объявлен
    исключением ниже, с причиной, а не защищается наравне с настоящими.

*   **Идентификаторы механизмов** — всё, что стоит в обратных кавычках. Пропажа есть
    ОТЧЁТ, а не запрет: сжатие законно снимает продублированные упоминания. Незаконна
    пропажа БЕЗ объяснения. Имена тестов выделены отдельно, потому что имя теста — это
    единственная нить от правила к гейту, который его держит: сняв его как «деталь»,
    правило перестаёт сообщать, чем держится.
"""

from __future__ import annotations

import json
import re
import sys

# Вымышленные идентификаторы: входят в множество, но их пропажа не есть отказ.
DECLARED_FICTIONAL = {
    "CB-9001": "пример чужой карты внутри правила CB-51 (импорт CSV), а не карта этого трекера",
}

CARD_RE = re.compile(r"\b(?:CB-\d+|T-\d+|ARCH-\d+|BT-\d+)\b")
# Кириллическая «Т» — ловушка: её в документе быть не должно. Считаем отдельно,
# чтобы молчаливое появление было видно, а не растворялось в латинском множестве.
CYRILLIC_T_RE = re.compile("\\bТ-\\d+\\b")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
TESTNAME_RE = re.compile(r"\b(?:test_[A-Za-z0-9_]+|Test[A-Za-z0-9_]+)\b")


def snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        md = fh.read()
    cards = sorted(set(CARD_RE.findall(md)))
    ticks = sorted({m.group(1).strip() for m in BACKTICK_RE.finditer(md)})
    tests = sorted(set(TESTNAME_RE.findall(md)))
    paths = sorted({t for t in ticks if "/" in t and not t.startswith("http")})
    return {
        "bytes": len(md.encode("utf-8")),
        "chars": len(md),
        "cards": cards,
        "cyrillic_T": sorted(set(CYRILLIC_T_RE.findall(md))),
        "backticks": ticks,
        "tests": tests,
        "paths": paths,
    }


def diff(before: dict, after: dict) -> int:
    rc = 0
    print(f"размер: {before['bytes']} → {after['bytes']} байт "
          f"({after['bytes'] - before['bytes']:+d}, "
          f"{100 * after['bytes'] / before['bytes']:.1f}% от исходного)")
    print(f"знаков: {before['chars']} → {after['chars']}")
    print()

    print("=== ГЕЙТ (отказ при пропаже): идентификаторы карт ===")
    b, a = set(before["cards"]), set(after["cards"])
    lost = sorted(b - a)
    gained = sorted(a - b)
    fam_b: dict[str, int] = {}
    for x in b:
        fam_b[_fam(x)] = fam_b.get(_fam(x), 0) + 1
    print(f"было {len(b)} различных: " +
          ", ".join(f"{k}-N: {v}" for k, v in sorted(fam_b.items())))
    real_lost = [x for x in lost if x not in DECLARED_FICTIONAL]
    fict_lost = [x for x in lost if x in DECLARED_FICTIONAL]
    if fict_lost:
        for x in fict_lost:
            print(f"  снят объявленный вымышленный: {x} — {DECLARED_FICTIONAL[x]}")
    if real_lost:
        rc = 1
        print(f"  ОТКАЗ: потеряно {len(real_lost)}: {real_lost}")
    else:
        print("  потерь настоящих идентификаторов нет")
    if gained:
        print(f"  добавлено {len(gained)}: {gained}")

    if after["cyrillic_T"]:
        rc = 1
        print(f"  ОТКАЗ: появились кириллические Т-N: {after['cyrillic_T']}")

    print()
    print("=== ОТЧЁТ (пропажа законна с объяснением): идентификаторы механизмов ===")
    for key, label in (("backticks", "токенов в обратных кавычках"),
                       ("tests", "имён тестов"),
                       ("paths", "путей")):
        b, a = set(before[key]), set(after[key])
        lost = sorted(b - a)
        print(f"{label}: {len(b)} → {len(a)}, пропало {len(lost)}")
        if key in ("tests", "paths") and lost:
            for x in lost:
                print(f"    − {x}")
    print()
    print("Полный список пропавших токенов в обратных кавычках — в T126-token-diff.txt")
    return rc


def main() -> int:
    if sys.argv[1] == "snapshot":
        with open(sys.argv[3], "w", encoding="utf-8") as fh:
            json.dump(snapshot(sys.argv[2]), fh, ensure_ascii=False, indent=1)
        s = snapshot(sys.argv[2])
        print(f"{s['bytes']} байт, {len(s['cards'])} карт, "
              f"{len(s['backticks'])} токенов, {len(s['tests'])} имён тестов, "
              f"{len(s['paths'])} путей")
        return 0
    if sys.argv[1] == "diff":
        with open(sys.argv[2], encoding="utf-8") as fh:
            before = json.load(fh)
        with open(sys.argv[3], encoding="utf-8") as fh:
            after = json.load(fh)
        return diff(before, after)
    raise SystemExit("usage: snapshot|diff")


if __name__ == "__main__":
    raise SystemExit(main())
