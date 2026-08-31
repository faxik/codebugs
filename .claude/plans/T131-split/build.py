#!/usr/bin/env python3
"""Build the four destination files from the chunk assignment.

Nothing here writes a sentence of its own except structural headings and the
signature block. Every other byte is an original chunk, concatenated in the
order it had in the source file.

THE ORACLE runs at the end: each of the 883 chunks must be findable, stripped,
in exactly the file it was assigned to. That is what makes "moved, not
rewritten" a checked claim instead of a promise.
"""
import json
import os
import re
import sys
from collections import defaultdict

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"
WT = "/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer/"

chunks = {u["id"]: u for u in json.load(open(SP + "chunks.json"))}
assign = {tuple(map(int, k.split(":"))): v
          for k, v in json.load(open(SP + "assign_final.json")).items()}

REF_OF = {}
for uid, u in chunks.items():
    s, sub = u["section"], u["subsection"]
    if s.startswith("Workflow") or s == "Releasing":
        REF_OF[uid] = "workflow.md"
    elif s == "Code rules" and sub == "Database":
        REF_OF[uid] = "database.md"
    elif s == "Code rules":
        REF_OF[uid] = "code-rules.md"
    elif s.startswith("Embeddings"):
        REF_OF[uid] = "embeddings.md"
    elif s.startswith("Architecture"):
        REF_OF[uid] = "architecture.md"
    else:                                   # Claims, Milestones, preamble
        REF_OF[uid] = "database.md"


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def collect(dest_filter):
    """Yield (unit, joined text) in document order for chunks matching dest."""
    for uid in sorted(chunks):
        u = chunks[uid]
        picked = [c for i, c in enumerate(u["chunks"]) if dest_filter(uid, assign[(uid, i)])]
        if picked:
            body = tidy("".join(picked))
            if body:
                yield u, body


# ---------------------------------------------------------------- root
# A HEADING FOLLOWS ITS CONTENT. If any chunk of a (sub)section stays in the
# root, that section's heading stays too, whatever the per-chunk vote said.
# Without this the surviving text silently re-parents itself under whichever
# heading happened to survive above it — the end-to-end read caught the CLI
# exit-code rules sitting under "### Testing", which is worse than either
# placing them or moving them, because it reads as an assertion about tests.
_root_sections = set()
for uid in sorted(chunks):
    u = chunks[uid]
    is_heading = "".join(u["chunks"]).lstrip().startswith("#")
    if any(assign[(uid, i)] == "root" for i in range(len(u["chunks"]))):
        if not is_heading:
            _root_sections.add((u["section"], u["subsection"]))
            _root_sections.add((u["section"], ""))
for uid in sorted(chunks):
    u = chunks[uid]
    if not "".join(u["chunks"]).lstrip().startswith("#"):
        continue
    if (u["section"], u["subsection"]) in _root_sections:
        for i in range(len(u["chunks"])):
            assign[(uid, i)] = "root"

root_out = []
for u, body in collect(lambda uid, d: d == "root"):
    root_out.append(body)
root_text = "\n\n".join(root_out) + "\n"
# a heading whose whole section left must not stay behind as an empty shell
lines = root_text.split("\n")
keep = []
for i, ln in enumerate(lines):
    if re.match(r"^#{1,4} ", ln):
        rest = [x for x in lines[i + 1:] if x.strip()]
        if not rest or re.match(r"^#{1,4} ", rest[0]):
            if not (rest and re.match(r"^#{1,4} ", rest[0]) and
                    len(ln) - len(ln.lstrip("#")) < len(rest[0]) - len(rest[0].lstrip("#"))):
                continue
    keep.append(ln)
root_text = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip("\n") + "\n"

# ------------------------------------------------------------- nested
def build_nested(dest, title, intro):
    # A section whose ORIGINAL heading travelled here must not also get a
    # generated one: the end-to-end read found "## Known architectural debt"
    # standing directly above "### Known architectural debt".
    own_heading = set()
    for uid in sorted(chunks):
        u = chunks[uid]
        if not "".join(u["chunks"]).lstrip().startswith("#"):
            continue
        if any(assign[(uid, i)] == dest for i in range(len(u["chunks"]))):
            own_heading.add(u["subsection"] or u["section"])

    parts = [f"# {title}", "", intro, ""]
    cur = None
    for u, body in collect(lambda uid, d, _dest=dest: d == _dest):
        sec = u["subsection"] or u["section"]
        if sec != cur:
            if sec not in own_heading:
                parts += [f"## {sec}", ""]
            cur = sec
        parts += [body, ""]
    return "\n".join(parts).rstrip("\n") + "\n"


ROOT_POINTER = (
    "The root `CLAUDE.md` is loaded in every session; this file is loaded only once you read a "
    "file in this directory. So it carries the RULES for this subsystem and their boundaries — "
    "never anything you must know before starting work, which stays in the root, and never the "
    "history behind a rule, which lives in `docs/claude-md-rationale/`."
)

nested_src = build_nested("nested", "codebugs source — subsystem rules", ROOT_POINTER)
nested_tests = build_nested("nested-tests", "codebugs tests — subsystem rules", ROOT_POINTER)

# ---------------------------------------------------------------- refs
refs = defaultdict(list)
for u, body in collect(lambda uid, d: d == "ref"):
    refs[REF_OF[u["id"]]].append((u["section"], u["subsection"], body))

SIGNATURE = """
## Что в этом файле, и чего в нём нет

**Что в этом файле.** Обоснования правил из корневого `CLAUDE.md`: почему правило появилось, какой
инцидент его породил, что показали раунды состязательного ревю, какие формы были отвергнуты и по
какому замеру. С T-131 сюда же переехала операционная глубина — устройство сторожей и хуков,
пределы алярмов, внутренности гейтов.

**Чего в этом файле НЕТ, и это важнее.** Здесь нет ни одного правила, которое нужно знать до начала
работы. Всё такое осталось в корневом `CLAUDE.md`, потому что этот файл не впрыскивается в сессию —
его открывает только тот, кого сюда послали. Если ты ищешь, как завести рабочее дерево, что значит
код отказа или что можно коммитить на `main`, — тебе не сюда, а в корень.

**Кто сюда ходит.** Тот, кто правит соответствующую подсистему, — и тот, кто собирается ослабить
правило и обязан сперва узнать, чем за него заплатили.
"""

written = {}
for fname, items in refs.items():
    path = f"docs/claude-md-rationale/{fname}"
    existing = open(WT + path).read() if os.path.exists(WT + path) else f"# {fname}"
    # IDEMPOTENT: drop anything a previous run of this script appended, or the
    # reference files grow by one whole copy on every rebuild (measured: 53K ->
    # 85K -> 147K over three runs before this guard existed).
    marker = "## \u0427\u0442\u043e \u0432 \u044d\u0442\u043e\u043c \u0444\u0430\u0439\u043b\u0435"
    if marker in existing:
        existing = existing[: existing.index(marker)]
    existing = existing.rstrip("\n")
    body = [existing, "", SIGNATURE.strip(), "",
            "---", "", "# Перенесено из корня юнитом T-131", ""]
    cur = None
    for sec, sub, text in items:
        key = f"{sec} / {sub}" if sub else sec
        if key != cur:
            body += [f"## {key}", ""]
            cur = key
        body += [text, ""]
    written[path] = "\n".join(body).rstrip("\n") + "\n"

written["CLAUDE.md"] = root_text
written["src/codebugs/CLAUDE.md"] = nested_src
written["tests/CLAUDE.md"] = nested_tests

for path, text in written.items():
    full = WT + path
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(text)

# ---------------------------------------------------------------- oracle
print("written:")
for p in sorted(written):
    print(f"  {p:<44}{len(written[p].encode()):>8} bytes")

contents = {p: open(WT + p).read() for p in written}
DEST_PATH = {"root": "CLAUDE.md", "nested": "src/codebugs/CLAUDE.md",
             "nested-tests": "tests/CLAUDE.md"}
lost = []
for (uid, i), d in assign.items():
    piece = chunks[uid]["chunks"][i].strip()
    if not piece:
        continue
    target = DEST_PATH.get(d) or f"docs/claude-md-rationale/{REF_OF[uid]}"
    probe = re.sub(r"[ \t]+\n", "\n", piece)
    probe = re.sub(r"\n{3,}", "\n\n", probe)
    if probe not in contents[target]:
        lost.append((uid, i, d, piece[:60]))
print(f"\nORACLE: chunks checked {len(assign)}, not found in their destination: {len(lost)}")
for row in lost[:15]:
    print("   MISSING", row)
if lost:
    sys.exit(1)
print("every chunk is present, verbatim, in exactly the file it was assigned to")
