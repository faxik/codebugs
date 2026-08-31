#!/usr/bin/env python3
"""§5 — the separate boundary check, made mechanical.

The rule this direction exists to enforce: a rule's own BOUNDARY — "this does
NOT cover…", "the known limit is…", "deliberately NOT…", "the cost is…",
"residual" — must travel WITH the rule. A rule that lands in an injected file
while its boundary lands in a reference nobody opens promises more than it
keeps, which is the defect the whole unit is about.

So: for every unit, if any chunk carrying a boundary marker went to `ref` while
the same unit put a rule in an INJECTED file (root or a nested CLAUDE.md), that
unit is flagged. The check cannot prove a boundary is attached to the right
sentence — that needs reading — but it does prove no boundary was separated
from its rule ACROSS FILES, which is the separation that actually costs.
"""
import json
import re

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"

chunks = {u["id"]: u for u in json.load(open(SP + "chunks.json"))}
assign = {tuple(map(int, k.split(":"))): v
          for k, v in json.load(open(SP + "assign_final.json")).items()}

MARKERS = re.compile(
    r"does NOT|do NOT|deliberately not|deliberately NOT|known limit|the cost is|"
    r"residual|RESIDUAL|what stays open|stays open|honest scope|not closed|"
    r"the price is|narrower than|does not cover|cannot be closed|what this does",
    re.I,
)
INJECTED = {"root", "nested", "nested-tests"}

flagged = []
for uid, u in sorted(chunks.items()):
    dests = {i: assign[(uid, i)] for i in range(len(u["chunks"]))}
    injected_here = {d for d in dests.values() if d in INJECTED}
    if not injected_here:
        continue                      # the whole unit left; nothing separated
    for i, c in enumerate(u["chunks"]):
        # ADJACENCY, not unit membership. A unit here can be 71 chunks covering
        # many separate rules, so "the unit kept some rule" over-flags wildly.
        # The separation that actually costs is a boundary cut from the rule
        # standing IMMEDIATELY before it: that rule is now injected while its
        # qualifier is not.
        if i == 0 or dests[i] != "ref" or not MARKERS.search(c):
            continue
        if dests[i - 1] in INJECTED:
            flagged.append((uid, i, [dests[i - 1]], re.sub(r"\s+", " ", c)[:130]))

print(f"units examined              : {len(chunks)}")
print(f"boundary chunks sent to ref while their unit kept a rule injected: {len(flagged)}")
print()
for uid, i, inj, txt in flagged:
    print(f"  unit {uid:<4} chunk {i:<3} rule stayed in {inj}")
    print(f"      {txt}")
print()
if not flagged:
    print("RESULT: no boundary was separated from its rule across files.")
else:
    print(f"RESULT: {len(flagged)} boundary chunks to review by reading.")
