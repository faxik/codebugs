# CB-18 + CB-15 — the `update` surface silently discarded caller data

Branch `fix/cb-18-append-note`, based on `feature/entity-claims` (`d13b3c6`).

## Why one tree

Predicate 1, **same root cause at the level that matters**: both cards describe the MCP `update`
surface accepting a caller's text and reporting success while discarding it. CB-18 is *exactly and
only* CB-15's first defect — a strict duplicate, so this is dedup, not clustering.

Two independently landable edits, under the ceiling of four:

| Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|
| Forward an existing core parameter to both surfaces | `findings.py` MCP wrapper, CLI handler + parser | CB-18, CB-15(1) | `TestAppendNoteIsReachable`, regenerated golden schema |
| Refuse unknown argument names at the server boundary | `server.py` (new `install_strict_arguments`) | CB-15(2) | `tests/test_server.py` |

CB-17 (severity immutable) was **deliberately kept out** even though Codex judged it eligible. It
needs a core parameter plus validation, and it carries genuine product questions (should retriage
record actor/reason/history?). The hostage test decides it: if CB-17 stalled on that decision, these
two finished edits would be waiting behind it.

## Reproducers (both defects, against pre-fix code)

```
DEFECT 1: update properties: [finding_id, status, notes, tags, meta_update, reported_at_ref]
          append_note exposed: False
DEFECT 2: update(finding_id="CB-1", note="APPENDED TEXT")
          is_error: False, full success payload returned
          meta.notes after call: 'ORIGINAL INVESTIGATION'   <- the text vanished
CONTROL:  update(finding_id="CB-1", status="not_a_status")
          RAISED ToolError: Invalid finding status
```

A bad **value** errors; a bad **name** vanishes. That asymmetry is the defect.

## Root cause — defect 2

`mcp/server/mcpserver/utilities/func_metadata.py`: `ArgModelBase.model_config` is
`ConfigDict(arbitrary_types_allowed=True)` and never sets `extra`, so pydantic's default
`extra="ignore"` applies. `arg_model.model_validate()` drops unknown keys and
`model_dump_one_level()` returns only declared fields, so the tool function — which has no
`**kwargs` — is called without the extra and never notices.

**`additionalProperties: false` cannot fix this.** Verified empirically: injecting it into a live
tool's `input_schema` changed nothing, because the server never validates arguments against the
JSON Schema.

## Fix

1. **CB-18 / CB-15(1)** — declare `append_note` on the MCP `update` tool and forward it; add
   `--append-note` to the CLI; reword `notes` to say it REPLACES, so the destructive option stops
   reading like the safe one. The core parameter already existed and is unchanged.
2. **CB-15(2)** — `server.install_strict_arguments()` appends a middleware that, for `tools/call`,
   compares the supplied argument names against the tool's declared properties and raises
   `MCPError(INVALID_PARAMS, ...)` listing both the offending and the accepted names. Unknown tool
   names are delegated so the SDK's own error stays authoritative; other methods are untouched.

**Rejected approach:** mutating `server._tool_manager._tools[...].fn_metadata.arg_model` to rebuild
it with `extra="forbid"`. That is private API of an SDK this repo migrated 1.x→2.x last week.
`MCPServer.middleware` is public. Codex (gpt-5.6-sol) pushed back on my initial plan to defer this
defect entirely and pointed at the middleware; it was right, and deferring would have been the
cheap-substitute failure the workflow forbids.

## Risks

- `MCPServer.middleware` is documented as **provisional** — "the signature is expected to change
  before v2 is final". All SDK coupling is therefore confined to `install_strict_arguments`, with
  `tests/test_server.py` as its seam.
- The middleware is server-wide (66 tools). `test_every_registered_tool_is_covered` asserts the
  guard applies to the whole catalogue, so a future tool cannot silently opt out.
- The golden wire-schema had to be regenerated. Note `tests/dump_schema.py` must be run with
  `PYTHONPATH=src` from the worktree — a bare `python` picks up the editable install pointing at
  the MAIN checkout and would have produced a golden for the wrong tree.

## Out of scope

- CB-17 (severity immutable) — see above.
- Reporting *what changed* on a write (`changed: ["status"]`), CB-15's third suggestion. With
  unknown names now refused and `append_note` reachable, the silent-loss paths this card was filed
  for are closed; a change-manifest is an API addition, not a defect fix.
