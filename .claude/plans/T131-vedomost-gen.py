#!/usr/bin/env python3
"""Generate the T-131 movement register (ведомость, §3 п. 8).

Today it is the PLAN of record: every unit, its size, the share staying in the
root, and where the departing share goes. After the move it becomes the
register proper — the "boundary" column is filled by checking, for each rule
that had a named boundary, that the boundary travelled WITH the rule.
"""
import json

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"
OUT = ("/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer/"
       ".claude/plans/T131-vedomost.md")

units = {x["id"]: x for x in json.load(open(SP + "units.json"))}
verd = {int(k): v for k, v in json.load(open(SP + "verdicts_revised.json")).items()}
MILESTONE = {48: 0.9, 155: 0.8, 156: 0.8, 157: 0.85, 158: 0.95}

total = sum(u["bytes"] for u in units.values())
root = sum(units[i]["bytes"] * v["d"] for i, v in verd.items())


def destination(i, v):
    if v["d"] >= 0.999:
        return "корень целиком"
    if v["sub"] == "tests":
        return "корень + `tests/CLAUDE.md`"
    if v["sub"] == "src":
        return "корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему)"
    return "корень + `docs/claude-md-rationale/workflow.md`"


rows = []
for i in sorted(units):
    u, v = units[i], verd[i]
    stay = u["bytes"] * v["d"]
    rows.append(
        f"| {i} | {u['section'][:26]} | {u['subsection'][:18] or '—'} | {u['bytes']} | "
        f"{stay:.0f} | {u['bytes'] - stay:.0f} | {destination(i, v)} | |"
    )

with open(OUT, "w") as f:
    f.write("# Т-131 — ведомость переезда\n\n")
    f.write("**Состояние: ПЛАН. Текст ещё не двигался.** После переезда графа «граница»\n")
    f.write("заполняется предметом: для каждого правила, у которого была названная граница,\n")
    f.write("показывается, что граница осталась ПРИ правиле, а не уехала отдельно.\n\n")
    f.write(f"База: `CLAUDE.md` = **195 123 байта** (перемерено 2026-08-31 после посадки Т-127).\n")
    f.write("Единиц — 159, и покрытие файла ими доказано: сумма байтов единиц плюс межстрочные\n")
    f.write("переводы равна размеру файла точно (`T131-segment.py` падает, если нет).\n\n")
    f.write("## Итог\n\n")
    f.write(f"- остаётся в корне — **{root:.0f}** байт ({100*root/total:.1f} %)\n")
    f.write(f"- уезжает — **{total-root:.0f}** байт ({100*(total-root)/total:.1f} %)\n")
    f.write(f"- из уезжающего собственно милстоунного — **1 646** (1,5 %), решено НЕ выделять\n")
    f.write("  в отдельный файл: выигрыш три процента ценой четвёртого впрыскиваемого файла,\n")
    f.write("  и раздел работает ориентацией о существовании подсистемы.\n\n")
    f.write("## Построчно\n\n")
    f.write("| № | раздел | подраздел | байт | в корень | уезжает | адрес | граница |\n")
    f.write("|---:|---|---|---:|---:|---:|---|---|\n")
    f.write("\n".join(rows) + "\n")

print(f"written: {OUT}")
print(f"rows: {len(rows)}   root: {root:.0f}   leaving: {total-root:.0f}")
assert len(rows) == 159, "the register must carry every unit"
print("register covers all 159 units")
