#!/usr/bin/env python3
"""Segment root CLAUDE.md into classifiable units (П2).

A UNIT is the natural paragraph-sized thing a classifier can judge:
  - a top-level bullet together with its continuation lines
  - a standalone prose paragraph
  - a markdown table (kept whole)
  - a heading (carried as its own unit so headings are never silently lost)
Every byte of the file lands in exactly one unit; the script asserts that.
"""
import json, re, sys

SRC = "/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer/CLAUDE.md"
raw = open(SRC, "rb").read()
text = raw.decode("utf-8")
lines = text.split("\n")

units = []
section = "(preamble)"
subsection = ""
cur = None

def flush():
    global cur
    if cur is not None and any(l.strip() for l in cur["lines"]):
        units.append(cur)
    elif cur is not None:
        # whitespace-only block: attach its bytes to the previous unit so
        # nothing is lost from the byte accounting
        if units:
            units[-1]["lines"].extend(cur["lines"])
        else:
            units.append(cur)
    cur = None

def start(kind):
    global cur
    flush()
    cur = {"kind": kind, "section": section, "subsection": subsection, "lines": []}

for ln in lines:
    if ln.startswith("## "):
        flush(); section = ln[3:].strip(); subsection = ""
        start("heading"); cur["lines"].append(ln); flush(); continue
    if ln.startswith("### "):
        flush(); subsection = ln[4:].strip()
        start("heading"); cur["lines"].append(ln); flush(); continue
    if ln.startswith("# "):
        flush(); start("heading"); cur["lines"].append(ln); flush(); continue

    stripped = ln.strip()
    is_bullet = bool(re.match(r"^[-*] ", ln)) or bool(re.match(r"^\d+\. ", ln))
    is_table = stripped.startswith("|")

    if is_bullet:
        start("bullet"); cur["lines"].append(ln); continue
    if is_table:
        if cur is None or cur["kind"] != "table":
            start("table")
        cur["lines"].append(ln); continue
    if not stripped:
        if cur is None:
            start("blank")
        cur["lines"].append(ln); continue
    # ordinary text line
    if cur is None:
        start("para")
    elif cur["kind"] == "table":
        start("para")
    elif cur["kind"] == "blank":
        cur["kind"] = "para"
    cur["lines"].append(ln)

flush()

# byte accounting
total = 0
out = []
for i, u in enumerate(units):
    body = "\n".join(u["lines"])
    b = len(body.encode("utf-8"))
    total += b
    out.append({
        "id": i,
        "kind": u["kind"],
        "section": u["section"],
        "subsection": u["subsection"],
        "bytes": b,
        "text": body,
    })
# account for the newlines consumed by split/join between units
joined = len(units) - 1 if units else 0
print(f"file bytes            : {len(raw)}")
print(f"sum of unit bytes     : {total} (+{joined} join newlines = {total+joined})")
assert total + joined == len(raw), "BYTE ACCOUNTING FAILED — units do not tile the file"
print("byte accounting       : OK, units tile the file exactly")
print(f"units                 : {len(out)}")

json.dump(out, open("/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/units.json", "w"))

from collections import defaultdict
agg = defaultdict(lambda: [0, 0])
for u in out:
    agg[u["section"]][0] += u["bytes"]
    agg[u["section"]][1] += 1
print()
print(f"{'section':<28}{'bytes':>9}{'share':>8}{'units':>7}")
for s, (b, n) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
    print(f"{s:<28}{b:>9}{100*b/len(raw):>7.1f}%{n:>7}")
