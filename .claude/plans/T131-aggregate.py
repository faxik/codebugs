#!/usr/bin/env python3
"""Aggregate the four classifiers' verdicts into the П2 number.

Reads verdict files verdicts_[ABCD].txt of pipe-separated lines:
    id|class|subsystem|d_frac|r_frac|reason
Refuses to report anything unless every one of the 159 units is covered
exactly once — a partial classification must not be able to look like an answer.
"""
import json, sys, glob, re
from collections import defaultdict

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"
units = {u["id"]: u for u in json.load(open(SP + "units.json"))}
total_bytes = sum(u["bytes"] for u in units.values())

verd = {}
dupes = []
for path in sorted(glob.glob(SP + "verdicts_*.txt")):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("TOTAL_UNITS"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            uid = int(parts[0].strip())
        except ValueError:
            continue
        if uid in verd:
            dupes.append(uid)
        try:
            d = float(parts[3]); r = float(parts[4])
        except ValueError:
            print(f"UNPARSEABLE FRACTIONS on id={uid}: {line[:80]}"); sys.exit(2)
        verd[uid] = {"cls": parts[1].strip().upper(), "sub": parts[2].strip().lower(),
                     "d": max(0.0, min(1.0, d)), "r": max(0.0, min(1.0, r)),
                     "why": "|".join(parts[5:])[:120]}

missing = sorted(set(units) - set(verd))
print(f"units total = {len(units)}   classified = {len(verd)}   missing = {len(missing)}   dupes = {len(dupes)}")
if missing:
    print("MISSING IDS:", missing); sys.exit(1)
if dupes:
    print("DUPLICATE IDS:", sorted(set(dupes))); sys.exit(1)
print("coverage OK — every unit classified exactly once\n")

root_bytes = sum(units[i]["bytes"] * v["d"] for i, v in verd.items())
# rationale sitting inside the material that stays in root
root_rationale = sum(units[i]["bytes"] * v["d"] * v["r"] for i, v in verd.items())

print(f"file bytes                       : {total_bytes}")
print(f"DIRECTIVE share (stays in root)  : {root_bytes:9.0f}  = {100*root_bytes/total_bytes:.1f}%")
print(f"DEPTH share (leaves root)        : {total_bytes-root_bytes:9.0f}  = {100*(total_bytes-root_bytes)/total_bytes:.1f}%")
print()
print("PREDICTED ROOT SIZE, two scenarios:")
print(f"  (1) directive/depth axis only        : {root_bytes:9.0f} bytes")
print(f"  (2) also extracting the WHY from what stays")
print(f"      rationale inside the kept material: {root_rationale:9.0f}")
print(f"      root after both axes              : {root_bytes-root_rationale:9.0f} bytes")
print(f"  benchmark (autosorter root)           :     26291 bytes")
print()

sec = defaultdict(lambda: [0.0, 0.0, 0])
for i, v in verd.items():
    u = units[i]
    sec[u["section"]][0] += u["bytes"]
    sec[u["section"]][1] += u["bytes"] * v["d"]
    sec[u["section"]][2] += 1
print(f"{'section':<44}{'bytes':>8}{'->root':>9}{'keep%':>7}")
for s, (b, rb, n) in sorted(sec.items(), key=lambda kv: -kv[1][0]):
    print(f"{s[:43]:<44}{b:>8.0f}{rb:>9.0f}{100*rb/b:>6.0f}%")

print()
dest = defaultdict(float)
for i, v in verd.items():
    u = units[i]
    leaving = u["bytes"] * (1 - v["d"])
    if leaving > 0:
        dest[v["sub"] if v["sub"] in ("src", "tests") else "unlabelled"] += leaving
print("WHERE THE DEPARTING BYTES GO:")
for k, b in sorted(dest.items(), key=lambda kv: -kv[1]):
    print(f"  {k:<12}{b:>9.0f}")

json.dump({str(k): v for k, v in verd.items()}, open(SP + "verdicts.json", "w"))
print("\nverdicts.json written")
