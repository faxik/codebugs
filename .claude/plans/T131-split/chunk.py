#!/usr/bin/env python3
"""Chunk every unit into sentence-sized pieces for the split decision.

THE ONE PROPERTY THAT MATTERS: "".join(chunks) == unit text, byte for byte.
Everything downstream reassembles from these chunks, so verbatim movement is
guaranteed by construction rather than by trusting anyone not to paraphrase.
The quality of the sentence boundaries only affects how tidy a cut can be; it
can never affect fidelity. The script asserts the round trip and refuses to
write anything if it fails.

Splitting never happens inside a `code span`, because cutting one would both
corrupt the text and break the multiset oracle that tokenises on backticks
(CB-279).
"""
import json
import re

SP = "/tmp/claude-1000/-home-faxik-w-codebugs/ab1e123c-6859-4055-b74d-9d902ae39936/scratchpad/"


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that must never be cut: code spans, incl. multi-backtick."""
    spans = []
    for m in re.finditer(r"(`+)(?:(?!\1).)*?\1", text, flags=re.S):
        spans.append((m.start(), m.end()))
    return spans


def chunk_text(text: str) -> list[str]:
    protected = _protected_spans(text)

    def inside(pos: int) -> bool:
        return any(a < pos < b for a, b in protected)

    cuts = [0]
    # A boundary: sentence-final punctuation, then whitespace, then something
    # that starts a new sentence. Also cut at blank lines (paragraph breaks).
    for m in re.finditer(r"(?<=[.!?:])[ \t]*\n|(?<=[.!?])[ \t]+(?=[A-ZА-Я«**`\-])|\n\n+", text):
        pos = m.end()
        if pos <= cuts[-1] or inside(m.start()) or inside(pos):
            continue
        cuts.append(pos)
    cuts.append(len(text))
    out = [text[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]
    assert "".join(out) == text, "chunking is not lossless"
    return out


def main() -> None:
    units = json.load(open(SP + "units.json"))
    verd = {int(k): v for k, v in json.load(open(SP + "verdicts_revised.json")).items()}
    total_chunks = 0
    out = []
    for u in units:
        chunks = chunk_text(u["text"])
        assert "".join(chunks) == u["text"]
        total_chunks += len(chunks)
        out.append({
            "id": u["id"],
            "section": u["section"],
            "subsection": u["subsection"],
            "bytes": u["bytes"],
            "first_pass_keep_share": verd[u["id"]]["d"],
            "first_pass_subsystem": verd[u["id"]]["sub"],
            "chunks": chunks,
        })
    json.dump(out, open(SP + "chunks.json", "w"), ensure_ascii=False)
    sizes = sorted(len(c.encode()) for u in out for c in u["chunks"])
    print(f"units       : {len(out)}")
    print(f"chunks      : {total_chunks}")
    print(f"chunk bytes : median {sizes[len(sizes)//2]}  p90 {sizes[int(.9*len(sizes))]}  max {sizes[-1]}")
    print("round trip  : OK for every unit (asserted)")
    big = [(len(c.encode()), u["id"]) for u in out for c in u["chunks"] if len(c.encode()) > 1500]
    print(f"chunks over 1500 bytes: {len(big)} {sorted(big, reverse=True)[:5]}")


if __name__ == "__main__":
    main()
