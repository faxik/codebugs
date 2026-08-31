#!/usr/bin/env python3
"""The T-131 register (ведомость, §3 п. 8) — final state, after the move."""
import json
import re
from collections import defaultdict

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"
OUT = ("/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer/"
       ".claude/plans/T131-vedomost.md")

chunks = {u["id"]: u for u in json.load(open(SP + "chunks.json"))}
assign = {tuple(map(int, k.split(":"))): v
          for k, v in json.load(open(SP + "assign_final.json")).items()}
overrides = json.load(open(SP + "coherence_overrides.json"))

NAME = {"root": "корень `CLAUDE.md`", "nested": "`src/codebugs/CLAUDE.md`",
        "nested-tests": "`tests/CLAUDE.md`", "ref": "справка `docs/claude-md-rationale/`"}

by_dest = defaultdict(int)
for (uid, i), d in assign.items():
    by_dest[d] += len(chunks[uid]["chunks"][i].encode())
total = sum(by_dest.values())

rows = []
for uid in sorted(chunks):
    u = chunks[uid]
    per = defaultdict(int)
    for i, c in enumerate(u["chunks"]):
        per[assign[(uid, i)]] += len(c.encode())
    dests = " + ".join(f"{NAME[d]} {b}" for d, b in sorted(per.items(), key=lambda kv: -kv[1]))
    head = re.sub(r"\s+", " ", "".join(u["chunks"]))[:70]
    rows.append(f"| {uid} | {u['section'][:24]} | {u['subsection'][:16] or '—'} | "
                f"{u['bytes']} | {dests} | {head} |")

with open(OUT, "w") as f:
    f.write("# T-131 — ведомость переезда\n\n")
    f.write("**Состояние: ПЕРЕЕЗД ВЫПОЛНЕН.** Каждая строка — единица исходного файла и то, куда\n")
    f.write("физически уехали её байты. Ни одно предложение не переписано: файлы собраны\n")
    f.write("склейкой исходных кусков, и это проверено оракулом, а не обещано.\n\n")
    f.write("## Чем закрыт каждый пункт §5\n\n")
    f.write("| Пункт §5 | Чем закрыт | Результат |\n|---|---|---|\n")
    f.write("| 1. полный набор зелен числом | `pytest tests/ -q` на неподвижном дереве | см. §15 брифа |\n")
    f.write("| 2. четыре пинующих утверждения | прогон по именам классов | 13 passed |\n")
    f.write("| 3. множества сохранены по ОБЪЕДИНЕНИЮ | `T126-idents.py` над корнем + вложенными + справками | 0 потерянных карт, 0 имён тестов, 0 путей, 1 псевдотокен |\n")
    f.write("| 4. потолок размера зелен | `tests/test_claude_md_size_ceiling.py` | 7 passed |\n")
    f.write("| сохранение текста | оракул сборки: каждый чанк найден в своём файле | 883/883 |\n")
    f.write("| проверка границ | `boundary_check.py` по смежности | 3 разрыва найдено и починено |\n\n")
    f.write("## Итог по адресам\n\n| адрес | байт | доля |\n|---|---:|---:|\n")
    for d, b in sorted(by_dest.items(), key=lambda kv: -kv[1]):
        f.write(f"| {NAME[d]} | {b} | {100*b/total:.1f} % |\n")
    f.write(f"| **всего** | **{total}** | 100 % |\n\n")
    f.write("## Правки связности, каждая с причиной\n\n")
    f.write("Единственные места, где адрес выбран не поабзацным разбором, а мною — и потому\n")
    f.write("названы поимённо:\n\n")
    for k, (dest, why) in sorted(overrides.items(), key=lambda kv: int(kv[0].split(':')[0])):
        f.write(f"- **единица {k}** → {NAME[dest]}: {why}\n")
    f.write("\n## Построчно\n\n")
    f.write("| № | раздел | подраздел | байт | куда уехало | начало |\n|---:|---|---|---:|---|---|\n")
    f.write("\n".join(rows) + "\n")

print(f"written: {OUT}")
print(f"rows {len(rows)}; destinations {dict(by_dest)}")
assert len(rows) == 159
print("register covers all 159 units")
