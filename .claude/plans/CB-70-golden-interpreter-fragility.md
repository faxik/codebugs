# CB-70 — the MCP wire-schema gate fails on every interpreter the package claims to support

Iteration: bugfix-loop 2026-08-17 (iteration 3), focus `codebugs` (pure/simple/confident).
Branch: `fix/cb-70-golden-interpreter-fragility`.

**Revision 2** — revision 1 proposed `inspect.cleandoc` and was **FAILED** by Codex, correctly. Its
central claim ("cleandoc is idempotent on 3.13 output") is false: cleandoc rewrites **61 of 68**
golden descriptions. Revision 2 uses a replica of CPython 3.13's own dedent instead, which is
measurably better on every axis. See *Review history*.

## Reproducer — run this session, in the worktree
```
UV_PROJECT_ENVIRONMENT=.venv312 uv run --python 3.12 --extra dev \
    python -m pytest tests/test_boundary.py -q
```
→ `FAILED …TestMcpWireSchema::test_schema_matches_golden`, reporting **64 drifted tools** —
effectively every tool with a multi-line docstring. Added and removed are both empty, so the whole
diff is whitespace.

Mechanism, probed directly rather than assumed:
```
Python 3.12.13: 'First line.\n\n    Indented body line.\n    '
Python 3.13.3:  'First line.\n\nIndented body line.\n'
```
CPython 3.13 strips docstring indentation at compile time; 3.11 and 3.12 do not. Nothing in the mcp
SDK dedents, so `Tool.description` carries whatever the compiler produced.

## Root cause, and why this is worse than a cosmetic diff
`pyproject.toml:11` declares `requires-python = ">=3.11"` and ruff targets `py311`, so 3.11 and
3.12 are supported — but the golden was generated on 3.13, so the gate cannot pass on either.
**The failure message is actively harmful**: it says *"Regenerate golden if intentional"*, and a
developer on 3.12 who follows it rewrites the golden into the indented form, breaking it for
everyone on 3.13.

A second, structural fault sits underneath. **The dump logic exists twice** — in
`tests/dump_schema.py` (generator) and copied into `TestMcpWireSchema._dump_current_schema`
(comparator). Two copies of the thing whose entire job is to agree is this repo's named
anti-pattern, and it is why a one-sided fix would silently do nothing.

## Evidence (read or run directly this session)
- `tests/test_boundary.py:145-213` — the gate, its private dump copy, the regenerate message.
- `tests/dump_schema.py` — generator; `asyncio.run(main())` executes at import, so it cannot
  currently be imported by a test.
- `pyproject.toml:11,41` — `requires-python = ">=3.11"`, `target-version = "py311"`.
- `src/codebugs/server.py:162` — production registers the raw functions, so the wire text really
  does follow the interpreter (this is what makes the residual below real, not hypothetical).
- **Measured, this session:** `inspect.cleandoc` changes 61/68 golden descriptions; the 3.13-dedent
  replica changes **0/68** on 3.13 and makes 3.12's output **byte-identical to the existing
  golden — 68/68, zero differences**.

## Plan
**The technically correct fix:** make the comparison a function of the tool surface rather than of
the compiler that built it, *without* loosening what the gate compares — and give generator and
comparator one implementation so they cannot drift.

**Why not `inspect.cleandoc`** (revision 1's choice, and the card's own suggestion): it normalizes
*more* than the compiler does — it also strips leading/trailing blank lines and expands tabs. Two
costs, one of them fatal: it would force a whitespace-only regeneration of 61 of 68 golden
descriptions, a diff large enough to hide a real change inside; and it would permanently blind the
gate to boundary-blank-line and tab changes that clients do see today.

**Adopted: replicate CPython 3.13's compile-time dedent** — compute the minimum indentation over
the non-blank lines *after the first*, strip exactly that prefix from those lines, leave the first
line and everything else untouched. Verified against reality rather than against my reading of
CPython: applied on 3.12 it reproduces the 3.13 golden byte-for-byte for all 68 tools, and on 3.13
it is a no-op for all 68. So the golden is **not regenerated**, and the gate keeps comparing tabs
and boundary blank lines exactly as it does today.

1. New `tests/_mcp_schema.py` — the shared collector, exporting the dedent helper and
   `collect_tool_schemas(providers=None)`. The `providers` seam exists so a test can inject a
   synthetic provider; default is `db.get_tool_providers(mode="all")`.
2. `tests/dump_schema.py` becomes a thin script over it, with its runner guarded by
   `if __name__ == "__main__":`.
3. `TestMcpWireSchema` calls the shared collector instead of its private copy.
4. Fix the regenerate instruction to carry `PYTHONPATH=src`, which CLAUDE.md documents as
   mandatory from a worktree and which the current message omits.
5. Re-scope the gate's own docstring: it compares the **canonical** tool surface, with
   interpreter-dependent docstring indentation normalized out. A reader must not think it pins the
   exact bytes a 3.12-hosted server emits, because it does not.

6. **Pin the golden as a fixed point of the normalizer** — a test asserting every golden description
   is already normalized. Fixing the generator and comparator does not stop a golden that was
   hand-edited or produced by an older script from sitting in the un-normalized form, and the gate
   would then fail identically. This is the only part of the fix that stays checkable without a
   second interpreter, which matters because there is no CI (CB-59).
7. Fix **all three** regenerate-instruction sites (the generator docstring, the test docstring, and
   the failure message itself, which carried no command at all), and give the failure message
   field-level detail so a reader can tell whitespace from a real surface change without regenerating.

### The residual — filed, not waved away
`server.py:162` registers the raw functions, so a server *hosted* on 3.11/3.12 really does send
indented descriptions to clients. That is presentational (no client parses description
indentation), but it is a supported-runtime behavior difference, and Codex was right that
recording it only in prose is insufficient. **Filed as its own card**; fixing it belongs at the
registration/exposure seam, which is CB-66's territory, not inside a test-only change.

## Risks & out-of-scope
- The dedent replica is a reimplementation of a CPython compiler detail. Mitigation is empirical,
  not textual: the 68-tool corpus comparison above is the test, and it is re-run in CI-less form by
  running the gate under 3.12 before finish.
- Out of scope: CB-66 (exposure layer), CB-59 (no CI, so no interpreter matrix — that is where a
  permanent 3.11/3.12/3.13 job belongs), and any change to what the server sends.
- **Not clustered with anything**: no shared root cause, edit seam, or atomic landing with any
  other open card.

## Verification — and the vacuity traps
- **Discriminating unit test.** The failure only manifests on 3.11/3.12, so running the gate proves
  nothing on a 3.13 machine. The test instead *constructs* the 3.12 condition: assign an indented
  string to a function's `__doc__` after definition (identical input on every interpreter), register
  it through an injected provider, and assert the collector emits the dedented form. Fails red on
  3.13 against the unfixed tree, which is the only machine available to run it.
  - **Trap named by review:** it must first assert the SDK's *raw* description is still indented.
    Without that, a future SDK version that dedents on its own would make the test pass vacuously.
  - **Second trap:** the collector iterates registry providers, so a synthetic tool registered
    elsewhere would never be observed — hence the injectable `providers` seam rather than a global.
- **The real gate under 3.12**, which is the card's actual requirement, and under **3.11** as well.
- The golden must remain **byte-identical** — `git diff` on it must be empty at finish.
- Full suite + `ruff check` under 3.13 via `tools/worktree-finish.sh`.

## Review history
**Revision 1 — Codex verdict FAIL.** Findings, all accepted:
1. *BLOCKING* — cleandoc weakens the gate: boundary blank lines and tab/space changes are visible
   to clients today and would become invisible.
2. *BLOCKING* — the idempotence claim was false. I had measured 61/68 independently before reading
   this, which is why it is stated as fact above rather than as a reviewer's opinion. Its proposed
   remedy — port 3.13's indentation-only pass, leaving the golden untouched — is what revision 2
   does, and it measured out perfectly (68/68 byte-identical on 3.12, 0/68 changed on 3.13).
3. *SERIOUS* — the wire residual is a real supported-runtime defect (`server.py:162` registers raw
   functions); prose is insufficient, file it. Done.
4. *MINOR* — `tests/_mcp_schema.py` + thin script is cleaner than importing the script itself, and
   the collector should not move into `src/` — snapshot policy is not product API. Adopted.
5. *SERIOUS* — `__doc__` assignment does work with the locked mcp 2.0.0, but the collector only
   walks registry providers, so the synthetic tool needs an injected seam; and assert the raw
   description first or a future SDK dedent makes the test vacuous. Adopted both.
6. *MINOR* — no other exact description snapshot exists in the repo; verify 3.11 too; no
   `pyproject.toml` change needed. Adopted.

**Revision 1 — Opus adversary, in parallel: PASS WITH CHANGES.** It attacked revision 1 and reached
finding 1 independently, by a different method (`ast.get_docstring(clean=False)` over all 259
docstrings in `src/`, versus my 68-tool wire comparison) with the same conclusion — which is the
strongest evidence available that the exact normalizer is right. Its additions, all adopted:
- **The golden must be pinned as a fixed point.** The sharpest finding of either review: fixing the
  generator and the comparator still leaves a badly-produced golden able to reintroduce the exact
  harm this card is about, and only this assertion survives the absence of CI.
- **The residual is not cosmetic, and my card said it was.** MCP clients render descriptions as
  Markdown, and CommonMark turns a 4-space-indented line after a blank line into an indented code
  block — so on 3.11/3.12 the prose body of ~61 tools renders as monospaced code. I verified the
  count myself (61 of 68 descriptions contain a blank line) and corrected CB-73: severity raised
  low → medium, framing rewritten. Filing it with the wrong reason would have got it dismissed.
- Three regenerate-instruction sites, not one; the failure message carries no command at all and no
  field-level detail; `tests/__init__.py` is load-bearing for the shared import and reads as cruft;
  the per-provider server topology is deliberate and differs from `server.py`'s shared one, so the
  golden cannot catch a cross-provider name collision.
- It also verified the SDK snapshots `__doc__` at registration (`Tool.from_function`), so the
  discriminating test's assign-then-register ordering is load-bearing — a decorator-style rewrite
  would silently measure a placeholder.

Where I did **not** follow it: it recommended hoisting the normalizer into `src/codebugs/` so a
future CB-73 fix could share it. That is building a seam for a consumer that does not exist, which
this repo refused on CB-44 and only built on CB-45 when the consumer arrived. The pointer is
recorded on CB-73 instead.
