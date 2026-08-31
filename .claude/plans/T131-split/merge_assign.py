#!/usr/bin/env python3
"""Harvest chunk assignments from the split agents' own transcripts.

Read from the transcripts rather than retyped by hand, deliberately: this
session earlier wrote an assignment block BEFORE the agent it attributed it to
had reported, and then read the resulting mismatch as the agent's failure. The
lesson is mechanical, so the fix is mechanical — the numbers come from the
agent's own output or they do not come at all.

A transcript is accepted only if every triple it carries falls inside ONE slice
and it covers that slice exactly. That rejects this session's own transcript
(which quotes several slices) and any partial or draft-laden run.
"""
import glob
import json
import os
import re
import sys

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"
TASKS = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/tasks/"
SLICE_NAMES = ("W1", "W2", "DB1", "DB2", "CR", "REST")

chunks = {u["id"]: len(u["chunks"]) for u in json.load(open(SP + "chunks.json"))}
slices = {n: {u["id"] for u in json.load(open(SP + f"chunks_{n}.json"))} for n in SLICE_NAMES}
expected = {n: {(u, i) for u in ids for i in range(chunks[u])} for n, ids in slices.items()}

accepted: dict[str, dict[tuple[int, int], str]] = {}
for path in sorted(glob.glob(TASKS + "*.output")):
    txt = open(path, encoding="utf-8", errors="replace").read()
    trip: dict[tuple[int, int], str] = {}
    for m in re.finditer(r"(\d+):(\d+):(root|nested-tests|nested|ref)", txt):
        uid, ci = int(m.group(1)), int(m.group(2))
        if uid in chunks and ci < chunks[uid]:
            trip[(uid, ci)] = m.group(3)
    if not trip:
        continue
    for name, exp in expected.items():
        if set(trip) == exp:
            if name in accepted:
                print(f"  note: {name} already harvested; ignoring {os.path.basename(path)}")
            else:
                accepted[name] = trip
                print(f"  accepted {os.path.basename(path)[:18]} as slice {name} ({len(trip)} chunks)")
            break

merged: dict[tuple[int, int], str] = {}
for name, trip in accepted.items():
    merged.update(trip)

all_expected = {(u, i) for u in chunks for i in range(chunks[u])}
missing_slices = [n for n in SLICE_NAMES if n not in accepted]
print()
print(f"slices harvested : {sorted(accepted)}")
print(f"slices missing   : {missing_slices}")
print(f"chunks assigned  : {len(merged)} / {len(all_expected)}")

json.dump({f"{u}:{i}": d for (u, i), d in merged.items()},
          open(SP + "assign_merged.json", "w"))
if missing_slices:
    print("\nINCOMPLETE — not ready to assemble.")
    sys.exit(1)
print("\ncomplete: every chunk of the file has a destination")
