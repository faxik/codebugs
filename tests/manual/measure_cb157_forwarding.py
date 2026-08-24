#!/usr/bin/env python3
"""CB-157 population survey: does every DECLARED MCP parameter reach the body?

Run from the worktree:
    uv run --extra dev python tests/manual/measure_cb157_forwarding.py

This is the REPORTING half. The predicates, the enumeration and the
self-deleting exceptions table all live in
`tests/test_declared_params_reach_the_body.py`, which is what actually gates;
importing them from there rather than restating them is deliberate, so the
number this prints and the number the suite enforces cannot drift apart.

WHAT IS MEASURED, over the whole tool registry (not an AST sweep for
`@mcp.tool` -- that spelling is blind to the 13 tools `surfacegen` emits from a
data declaration):

  read      the body LOADS the parameter's name at least once. SOUND: a name a
            body never loads provably cannot influence what the body does, so a
            violation is a defect and there are no legitimate exceptions.
  call_arg  the parameter is handed DIRECTLY to some call, positionally or by
            keyword, under any name. Stronger; its legitimate exceptions are the
            wrappers that ASSEMBLE the value into another argument.

Mutant M11 of the card -- hardcode `include_unanchored=False` in the body of
`anchor_recapture` while leaving it in the signature -- violates both.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from test_declared_params_reach_the_body import (  # noqa: E402
    ASSEMBLED_BY_THE_WRAPPER,
    _declared_params,
    _emitted_target,
    _is_emitted,
    _registered_tools,
    survey,
)


def main() -> None:
    registered = _registered_tools()
    emitted = {name for name, fn in registered.items() if _is_emitted(fn)}
    by_construction = {
        name for name in emitted if _emitted_target(registered[name])[0] == "calls"
    }
    declared_total = sum(len(_declared_params(fn)) for fn in registered.values())

    verdicts = survey()
    unread = [v for v in verdicts if not v.read]
    noncall = [v for v in verdicts if not v.call_arg]
    dynamic = {v.tool for v in verdicts if v.dynamic}

    print("--- enumeration (REGISTRY walk, the only sound one) ---")
    print(f"tools in the registry:        {len(registered)}")
    print(f"  handwritten @mcp.tool:      {len(registered) - len(emitted)}")
    print(f"  emitted by surfacegen:      {len(emitted)}")
    print(f"    ...forwarded by construction (calls=):  {len(by_construction)}")
    print(f"    ...followed into a manual handler:      {len(emitted) - len(by_construction)}")
    print(f"declared parameters (all tools):            {declared_total}")
    print(f"parameters with a body to judge:            {len(verdicts)}")
    print()
    print("--- predicates ---")
    print(f"READ by the body:             {len(verdicts) - len(unread)} / {len(verdicts)}")
    print(f"NOT read  (always a defect):  {len(unread)}")
    print(f"reaches a call:               {len(verdicts) - len(noncall)} / {len(verdicts)}")
    print(f"NOT a call arg:               {len(noncall)}")
    print(f"  ...declared as assembled:   {len(ASSEMBLED_BY_THE_WRAPPER)}")
    print(f"bodies using dynamic access:  {len(dynamic)}")
    print()
    print("--- NOT READ (a body that never loads the name cannot honour it) ---")
    for v in unread:
        print(f"  {v.tool}({v.param})  [{v.origin}]")
    if not unread:
        print("  (none -- CB-157's live population is EMPTY)")
    print()
    print("--- NOT HANDED TO A CALL (must each carry a declared reason) ---")
    for v in noncall:
        mark = "declared" if (v.tool, v.param) in ASSEMBLED_BY_THE_WRAPPER else "UNDECLARED"
        print(f"  {v.tool}({v.param})  [{v.origin}]  {mark}")


if __name__ == "__main__":
    main()
