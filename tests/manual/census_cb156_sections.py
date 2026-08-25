"""CB-156 census: enumerate Google-style sections in the tool descriptions ON THE WIRE.

Reads `tests/golden/mcp_schema.json` — what clients actually receive — and reports,
BY LINES (never by splitting on an `Args:` token; that mismeasurement is recorded on
the card), the sections present, the continuation lines, and whether any description
would produce a CommonMark indented code block.

Run:  python3 tests/manual/census_cb156_sections.py [path/to/mcp_schema.json]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

# Discovery is OPEN-ENDED on purpose: a line at column 0 whose entire content is a
# word (or words) followed by a colon. No closed list of section names — measuring
# with a closed list is the very defect this direction catches elsewhere.
HEADER = re.compile(r"^([A-Za-z][A-Za-z ]*):\s*$")


def census(descriptions: list[str]) -> dict:
    sections: Counter[str] = Counter()
    with_any_section = 0
    with_continuation = 0
    code_block_shape = 0
    lazy_continuation = 0
    bullets = 0

    for desc in descriptions:
        lines = desc.split("\n")
        found: set[str] = set()
        cont = False
        lazy = False
        for i, line in enumerate(lines):
            m = HEADER.match(line)
            if m and i + 1 < len(lines) and lines[i + 1].startswith("    "):
                found.add(m.group(1))
                # A section header immediately followed by an indented line, with no
                # blank line between, is a LAZY PARAGRAPH CONTINUATION in CommonMark.
                if lines[i + 1].strip():
                    lazy = True
            if line.startswith("        ") and line.strip():
                cont = True
            # blank line, then a >=4-space indent => CommonMark indented code block
            if i > 0 and not lines[i - 1].strip() and line.startswith("    ") and line.strip():
                code_block_shape += 1
            if re.match(r"^\s*[-*+] ", line):
                bullets += 1
        sections.update(found)
        if found:
            with_any_section += 1
        if cont:
            with_continuation += 1
        if lazy:
            lazy_continuation += 1

    return {
        "tools": len(descriptions),
        "sections": dict(sections.most_common()),
        "descriptions_with_any_section": with_any_section,
        "descriptions_with_continuation_lines": with_continuation,
        "descriptions_with_lazy_continuation": lazy_continuation,
        "indented_code_block_shapes": code_block_shape,
        "bullet_lines": bullets,
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "tests/golden/mcp_schema.json"
    tools = json.load(open(path))
    print(json.dumps(census([t.get("description") or "" for t in tools]), indent=2))


if __name__ == "__main__":
    main()
