# Load-bearing, despite being otherwise empty: it makes `tests` a package, which
# is what lets pytest import test modules as `tests.*` and lets them import each
# other (`from tests._mcp_schema import ...`). Deleting it as apparent cruft
# breaks the MCP wire-schema gate's import.
