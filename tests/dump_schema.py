"""Dump MCP tool schemas as a flat sorted list for regression diffing.

Regenerate the golden file with:
    PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json

PYTHONPATH=src is not optional from a worktree: without it a bare `python`
resolves `codebugs` through the editable install pointing at the MAIN checkout,
and the golden would snapshot a tree you did not touch.

The collection itself lives in `tests/_mcp_schema.py`, shared with the gate in
`tests/test_boundary.py` so the two cannot disagree about what the surface is.
"""

import json
import pathlib
import sys

# Run as a script, sys.path[0] is tests/, not the repo root — so the package
# spelling `tests._mcp_schema` would not resolve. Add the root and use that one
# spelling everywhere rather than importing the same module two different ways.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._mcp_schema import collect_tool_schemas  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(collect_tool_schemas(), indent=2, sort_keys=True))
