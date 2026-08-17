# RFC: The exposure layer — one contract per capability, both surfaces generated

**Date:** 2026-08-17 · **Status:** Reviewed (adversarial-review-x2 applied; see appendix) ·
**Category:** architecture / deepening · **Health:** 8/10 as amended (5/10 pre-review)

## Problem

Every domain capability that exists on both surfaces is written four times:

1. The domain function — the real contract (`query_requirements`, `src/codebugs/reqs.py:284`,
   keyword-only args after `conn`).
2. An `@mcp.tool()` wrapper inside `register_tools(mcp, conn_factory)` (`reqs.py:646–855`;
   provider registered at `:858`). Its Python signature and docstring ARE the wire schema: the
   mcp 2.x SDK derives `inputSchema` from annotations/defaults via pydantic, `outputSchema` from
   the **return annotation**, and the docstring becomes the tool `description` — pinned
   **structurally** (name, description, inputSchema; outputSchema after the baseline amendment
   below) by `tests/golden/mcp_schema.json` via `tests/test_boundary.py::TestMcpWireSchema`.
3. An argparse parser block inside `register_cli(sub, commands)` (`reqs.py:863–1097`).
4. A `_cmd_*` handler mapping `args.foo` back to kwargs and rendering via `fmt.format_table`.

This adapter tissue is 35–41% of `reqs.py` / `sweep.py` / `bench.py` by line count (40.7 / 35.9 /
35.3% measured). **Scope, stated precisely** (the first draft overclaimed; corrected after
review): the tree has **68 MCP tools and 54 domain CLI commands** (56 with `init`/`where`). The
"CLI is a degraded hand-copy of the MCP wrapper" premise holds for the **~25 capabilities present
on both surfaces**; ~24 tools are genuinely MCP-only (all of `embeddings`, most of `milestones`),
~31 commands have no tool at the corresponding name, and `merge` / `provenance` have **zero**
name overlap between their tools and their commands. For the shared ~25 the layer buys parity by
construction; for the rest it buys uniform plumbing, a first CLI for embeddings, and one
registration pattern.

Where both surfaces exist, the copies **provably drift** — and the population is larger than any
example list (this repo's standing lesson, now applied to itself):

- `_cmd_reqs_query` (`reqs.py:898`) forwards no `source`, `tag`, or `offset`, and lacks the
  `status="deferred"` branch its MCP twin has (`reqs.py:754–781`): the MCP tool resolves the
  pseudo-status through `blockers.deferred_id_restriction` (CB-28) and annotates `blocker_count`;
  the CLI raises `Invalid requirement status: 'deferred'`.
- A mechanical audit finds **~17 declared-but-unreachable parameters** across the tree: findings
  `update` missing `tags`/`meta_update`/`reported_at_ref` (`findings.py:1974–1979` vs
  `:1377–1385`); findings `query` missing `tag`/`meta_key`/`meta_value`/`commit`/`ref`/`offset`
  (`:1981–1990` vs `:1426–1443`); findings `add` missing `reported_at_ref`; reqs `add` missing
  `meta`; reqs `update` missing `section`/`tags`/`meta_update`; `claims list` never forwards
  `limit` (`claims.py:757`); `milestone-audit` never forwards `--since`
  (`milestones/__init__.py:483–489`).
- Validation that lives **only in the MCP wrapper** leaves the CLI accepting contradictory input:
  `codebench_import`'s csv-XOR-json check exists in the wrapper alone (`bench.py:552–553`), so
  `codebugs bench-import data.csv --json-file other.json -b X` silently reads `other.json` and
  discards `data.csv`; `codebench_delete` is identical. (Cross-argument validation has no
  domain-layer home — the op body below is that home.)
- `embeddings.py` has four MCP tools and **no CLI registration at all**.
- The CSV-split idiom is hand-copied at 9 sites across 4 files — and they are **three different
  conventions** (`findings add --tags` keeps empty tokens, `query --id` filters them,
  `bench --runs` is `nargs="+"`); unifying is a behavior change at six of the nine.
- The CLI has **no dedicated test module**: eight test files shell out to the CLI (~90 sites,
  which is why `tests/conftest.py`'s root-clearing guard exists), but nothing tests the
  table-render path or `fmt.py` — which is how `claims.py:781` survived: it calls
  `fmt.format_table` with the arguments **swapped** *and* with rows as `list[list[str]]` where
  the function requires `list[dict]` (`fmt.py:6`) — two defects, either of which crashes
  `codebugs claims --format table` (filed as CB-64).
- The error-arm ordering contract (`json.JSONDecodeError` re-raised BEFORE the
  `(KeyError, ValueError)` arm — post-commit corruption is not bad input; pinned by
  `TestRetriageCliContract`) is a per-handler discipline; four `blockers` handlers catch nothing
  at all and traceback on any domain error.

The interface of this layer (four hand-synchronized restatements) is as complex as its
implementation — the definition of a shallow module.

## Proposed interface

**Core move (designs A and C converged on it independently; both review attackers verified the
mechanism end-to-end):** promote the MCP wrapper body to a module-level **op** function with
`conn` prepended as its first parameter. The op's signature (minus `conn`), **return annotation**,
docstring, and body ARE the single contract; the layer generates both surfaces from it.
**Parameter-set divergence between surfaces becomes unrepresentable** — there is deliberately no
knob that hides a declared parameter from one surface. Presentation divergence (names, defaults,
rendering, exit codes) stays expressible and *declared*.

New module `src/codebugs/expose.py` (stdlib only; ~450–550 lines). Public surface is three names:

```python
class Surface:
    """One per domain module. Registers both providers on the EXISTING registries and
    binds generated register_tools(mcp, conn_factory) / register_cli(sub, commands)
    into the owning module's namespace — the frozen module facade (REEXPORTED_NAMES,
    direct test callers) stays byte-compatible. install_strict_arguments untouched
    (review-verified)."""
    def __init__(self, domain: str, *,
                 error_prefix: str = "",                      # "" | "codebugs: " | "Error: "
                 manual_cli: Callable | None = None,
                 manual_cli_commands: dict[str, str] | None = None,   # cmd -> REASON
                 manual_tools: dict[str, str] | None = None,          # tool -> REASON
                 ) -> None: ...

    def op(self, fn=None, *,
           tool: str | None,            # MANDATORY, explicit. None = CLI-only.
           cli: str | None,             # MANDATORY, explicit. None = MCP-only.
           python_name: str | None = None,   # generated def's __name__ when it must differ
                                             # from the wire name (schema titles derive from
                                             # __name__; ten milestone tools are pinned to
                                             # underscore-prefixed titles in the golden)
           args: dict[str, Arg] | None = None,
           pre: Callable | None = None,      # CLI-side kwarg synthesis from the parsed
                                             # Namespace, runs BEFORE db.connect() —
                                             # cross-argument decisions (bench path/suffix),
                                             # flag-into-meta merges (findings --lines)
           connect: Callable | None = None,  # connection override: (ns) -> Connection
                                             # (provenance resolve-trailers' db.connect(repo))
           render: Renderer,                 # MANDATORY — no default. Ships json_render()
                                             # and json_render(sort_keys=True); the choice is
                                             # visible and reviewed per migration
           exit_code: Callable[[Any], int] | None = None,
           json_flag: bool = False,          # OPT-IN --json (never unconditional: similarity
                                             # and claims already own a --json with different
                                             # payloads)
           smoke: SmokeCase | None = None,   # declared smoke case; REQUIRED (or an explicit
                                             # skip_reason) for every generated op
           cli_help: str | None = None,
           ) -> Callable: ...
        # Returns fn unchanged (directly testable with an explicit conn).
        # REFUSES at decoration time: missing return annotation; an annotation shape
        # not in the derivation table; a bool-default-True param without Arg(help=...)
        # (inverted flags must not inherit affirmative help text); a module without
        # `from __future__ import annotations`; and ANY name collision — provider,
        # wire tool, CLI command, generated python name, or option string within a
        # command (the MCP ToolManager warns-and-keeps on duplicates, so Surface
        # must reject them itself).

@dataclass(frozen=True)
class Arg:
    positional: bool = False
    flags: tuple[str, ...] = ()
    required: bool = False
    choices: tuple[str, ...] | None = None
    nargs: str | int | None = None        # "+", "*", "?" — sweep/bench positionals
    dest: str | None = None               # similarity's as_json/family_limit
    metavar: str | None = None
    type: Callable | None = None          # float (similarity thresholds), etc.
    parse: Callable[[str], Any] | None = None   # value transform; failure -> exit 1
    cli_default: Any = _UNSET             # per-surface default (findings add:
                                          # source="claude" wire / "human" CLI)
    help: str | None = None
```

**The op contract, five lines:** first parameter is `conn`; every other parameter's name,
annotation, and default is exactly what the wire shows; **the return annotation is mandatory and
is exactly what the wire shows** (it produces `outputSchema`); the docstring is exactly the wire
description; the body may call domain functions and carry cross-surface enrichment, and is the
only place such logic may live. Ops take **content**, not paths — a path-taking CLI
(`bench-import`) is a `pre=` transformation, not a different parameter set.

### Generation mechanics

- **MCP side — exec-codegen a real `def`.** Generated **lazily inside `register_tools`** (matching
  the SDK's own `list_tools()`-time annotation resolution): a genuine
  `def <python_name or wire name>(<params minus conn>) -> <return annotation>` with defaults,
  annotation strings, and docstring copied verbatim, in a namespace seeded from `fn.__globals__`;
  body `with conn_factory() as conn: return fn(conn, **kwargs)`; `linecache`-registered.
  Registered via `mcp.tool()` bare, or `mcp.tool(name=<wire>)` **only when the wire name differs
  from the python name** (test fakes' `tool()` takes no `name=`). Pydantic titles both the
  argument model and the output model from `__name__` — hence `python_name`. This is the one
  genuinely magic function; documented as the only repair site on SDK change, same treatment as
  `install_strict_arguments`'s provisional-middleware note.
- **CLI side.** Parser derived from the signature through a **closed derivation table** —
  `str|None`, `int`, `float`, `bool` (default-False → `store_true`; default-True → `--no-<name>`
  requiring declared help), `list[str]|None` (CSV), required/positional collections via
  `Arg(nargs=)`, `dict|None` (JSON **parsed in the generated handler before `db.connect()`,
  failure → stderr + exit 1** — never as an argparse `type`, which exits 2), `str | list | None`
  passed through as string. A shape not in the table is **refused at declaration**, never guessed.
  A `nargs="*"` collection maps `[]` to `[]`, not `None` — the domain's explicit-empty semantic
  (`sweep.archive_items`) becomes CLI-reachable for the first time; legacy collapse is declared
  via `Arg(parse=...)`.
- **The standard handler**, written once: `pre=` synthesis → JSON/`parse` validation (exit 1) →
  `connect or db.connect()` → domain call inside the error ladder (`json.JSONDecodeError`
  re-raised before `(KeyError, ValueError)` → `error_prefix` + exit 1) → `finally: close()` →
  render **after** close (the deliberate unification onto the minority-but-correct pattern of
  `reqs.py:952` / `findings.py:1708`; the 20 handlers that render inside the `try` change which
  render-time exceptions are caught, enumerated per migration) → declared `exit_code` or 0.
  Contention → exit 5 stays central in `cli.py:146–155`. A `Renderer` must never touch the
  connection — it runs after close.

## Usage example

`reqs_query` with the CB-28 deferred enrichment — replaces `reqs.py:721–781`, `:898–941`, and the
parser block at `:1057–1064`:

```python
surface = expose.Surface("reqs")

@surface.op(tool="reqs_query", cli="reqs-query", render=_render_reqs_query)
def reqs_query(conn, id: str | None = None, ids: list[str] | None = None,
               status: str | None = None, priority: str | None = None,
               section: str | None = None, search: str | None = None,
               source: str | None = None, tag: str | None = None,
               group_by: str | None = None, limit: int = 100, offset: int = 0,
               ) -> dict[str, Any]:
    """<today's docstring from reqs.py:734–753, byte-for-byte>"""
    from codebugs import blockers
    deferred_ids = None
    if status == "deferred":
        deferred_ids = blockers.deferred_id_restriction(conn, ENTITY_REQUIREMENT, id=id, ids=ids)
        if not deferred_ids:
            # MUST NOT fall through as ids=[] — that reads as "no filter" (CB-28)
            return {"grouped": False, "total": 0, "limit": limit, "offset": offset,
                    "requirements": []}
        id, ids, status = None, deferred_ids, None
    result = query_requirements(conn, id=id, ids=ids, status=status, priority=priority,
                                section=section, search=search, source=source, tag=tag,
                                group_by=group_by, limit=limit, offset=offset)
    if deferred_ids is not None and not result.get("grouped"):
        counts = blockers.blocker_counts_for(conn, entity_type=ENTITY_REQUIREMENT,
                                             entity_ids=deferred_ids)
        for row in result["requirements"]:
            row["blocker_count"] = counts.get(row["id"], 0)
    return result
```

`codebugs reqs-query --status deferred --priority must --source Take26 --offset 50` now works, and
`_render_reqs_query` gains a conditional `blocker_count` column so the enrichment is visible in
table output, not only via `--json`. Milestones' spec-canonical names declare both halves:
`@surface.op(tool="pull_next", cli=None, python_name="_pull_next", ...)`. Per-surface default:
`args={"source": Arg(cli_default="human")}` with the op keeping `source: str = "claude"`.

## What the layer guarantees — and what it does not

The layer proves **surface → op**: every declared parameter reaches the op on both surfaces, or
the declaration fails. It does **not** prove **op → domain** — the op body is hand-written code
with exactly the drift surface the old wrapper had. **The per-domain forwarding tests
(`tests/test_server.py::test_severity_actually_reaches_the_database`,
`tests/test_similarity.py::test_tools_forward_arguments`) are RETAINED unchanged.** The layer
shrinks the number of forwarding sites from two per capability to one; it does not remove the
class. (Cross-model review finding; the first draft claimed otherwise.)

## Dependency strategy

**Category: in-process.** `expose.py` imports stdlib + `codebugs.db` only; never the mcp SDK
(the generated closure receives the server object as today); argparse lazily inside generated
`register_cli`. Zero SQL, no schema. No new dependencies. Tests run both surfaces against
in-memory / `tmp_path` SQLite as the suite already does.

## Testing strategy

- **Baseline first (precondition):** extend `tests/dump_schema.py` to record **`outputSchema`**
  and to iterate **every `SERVER_NAMES` mode**, then one reviewed baseline regen commit — the
  current golden is blind to output schemas (structured-output could be stripped from all 68
  tools with the gate green) and to per-mode catalogue membership (embeddings leaving
  `--mode reqs` would be invisible). "Zero regens" applies from that baseline.
- **New boundary tests** (`tests/test_expose.py`): wire-synthesis equivalence (synthesized vs
  hand-written twin, schemas equal — including outputSchema and titles); the derivation table and
  its refusals; the handler contract (arm ordering re-asserting `TestRetriageCliContract`
  against the generated ladder, render-after-close, exit codes, `pre=`/`connect=`, JSON exit-1);
  collision validation.
- **Declared smoke cases** per generated op (argv, setup, expected exit/result,
  destructive/read-only class, or an explicit `skip_reason`) — mandatory for every op the
  capability matrix marks "generated"; an opt-in floor with no coverage requirement would regrow
  the untested CLI this RFC exists to fix. They are *declared*, not auto: entity-dependent ops
  need fixtures, and `tests/conftest.py` nulls tracker roots.
- **Parity ratchet** reading **Surface-owned provenance** (the registries alone cannot tell
  generated from manual) with a **shrinking legacy allowlist** so it enforces from branch one:
  every tool and command is exposure-generated, declared manual with a reason
  (`manual_cli_commands` / `manual_tools`), or on the shrinking allowlist. Name-level parity
  only — parameter parity is guaranteed by the single signature for migrated ops, and op→domain
  forwarding stays with the retained domain tests.
- **Parser-snapshot characterization**: per command, capture flags/defaults/choices/help from the
  argparse tree before and after each module's migration — **the diff IS the behavior-change
  enumeration**, not a hand-written list. Every falsey-coercion site (`--limit 0` → 100 at
  `reqs.py:906`; `--by ""` → `"severity"` at `findings.py:1712`; `--batch-size 0` → 10 at
  `sweep.py:1023` where the domain raises) must appear as either preserved-via-`Arg` or
  changed-with-a-named-test. (The first draft called the pass-through "strictly more correct";
  review struck that — `limit=0` yields a silent empty queue, CB-25's own banned class.)
- **Recommended:** one real `call_tool` characterization per return-shape class (schema equality
  does not fully capture `convert_result`).

## Migration

- **Hard precondition — the capability matrix, checked in and ratcheted.** One row per current
  tool AND command: provider, wire name, Python name, CLI name, parameters, defaults, renderer,
  error text, exit codes, intended generated/manual status. Committed under `.claude/plans/`,
  and the parity ratchet validates registry contents against it. No migration branch before it
  exists. (A migration order is an enumeration; the matrix is the population. The review found
  the first draft's order had missed an entire registered module and six commands.)
- **Order** (one module per branch, worktree workflow, golden-vs-baseline untouched as the
  per-branch gate): `blockers` → `bench` → `sweep` → `similarity` → `embeddings` (ops declared on
  the **reqs Surface**, preserving `--mode reqs` and the provider registries exactly) →
  `provenance` (both `staleness_check` and `resolve-trailers`, the latter with `connect=`) →
  `merge` (5 tools + 4 commands, zero name overlap — all names explicit) → `reqs` → `findings` →
  `milestones` (python_name for the ten spec-canonical tools; `milestone-reconcile` CLI-only) →
  `claims` (tools + `who-holds` + `claims` list, both of which today lack the contention
  contract and gain it from the generated ladder with `exit_code=`/`render=`).
- **Declared hand-written remainder** (shrunk by review from 6–7 commands to 4): claims `claim` /
  `release` (the `_connect_or_undetermined` structured-UNDETERMINED protocol is a genuinely
  different handler contract) and findings `import-csv` / `export-csv` (file programs). Each in
  `manual_cli_commands` with its reason; the ratchet enforces.
- Behavior changes ride along **loudly** via the parser-snapshot diff; stderr-prefix unification
  (three prefixes today) is declared per Surface via `error_prefix`.

## Implementation recommendations (durable)

- The layer owns: surface generation, conn acquisition, error/exit protocol, coercion, collision
  refusal, provenance for the ratchet.
- The layer hides: SDK introspection mechanics, argparse, the CSV/JSON idioms, handler plumbing.
- The layer exposes: `Surface`, `Surface.op`, `Arg` — every knob evidenced by a named consumer
  (naming: all 43 mismatches; `python_name`: ten milestone tools; `render`: ~15 printers;
  `exit_code`: claims; `cli_default`: findings add; `nargs`/`dest`/`type`: sweep/bench/similarity;
  `pre=`: bench-import, findings `--lines`; `connect=`: resolve-trailers; `manual_*`: claims
  core, findings CSV I/O).
- Unknown shapes are refused at declaration, counted, and gated — never guessed (the letter must
  not decide).
- If a genuine need for parameter-set divergence between surfaces appears, the answer is opting
  that op out of the layer, not a divergence knob.

## Trade-offs accepted

- Line savings are **~10–15% in `reqs.py`, ~400–600 lines repo-wide** — roughly 70 declarative
  knobs replace ~90 parser statements and ~25 handler bodies; docstrings and renderers survive as
  real content. The product is one contract per capability plus a testable CLI, not deletion.
- Renderers stay per-command Python; a rendering DSL is rejected.
- exec-codegen is a concentrated failure point with a provisional-by-the-SDK's-own-admission
  introspection coupling; mitigated by the equivalence test, the extended golden, and single-site
  repair.
- The deferred-status path's double blocker scan and per-blocker status reads are **pre-existing
  MCP behavior, preserved, not addressed** — a one-pass blocker summary API is its own card
  (deferred out loud).
- The 3.11/3.12 docstring-dedent exposure of the *existing* golden (compiled-docstring dedent is
  a 3.13 compiler behavior) predates this RFC and is filed separately.

---

## Appendix: Adversarial Review x2 Corrections (2026-08-17)

Reviewed by an Opus adversary and an independent Codex (GPT-5.6 Sol) attacker in parallel, an
Opus defender, and an Opus judge. As written: 3 FATAL / 15 SERIOUS / 8 WEAKNESS / 4 NITPICK
(Opus) + 11 major risks (Codex). Judge: **5/10 as written, 8/10 as amended**; every FATAL and
SERIOUS resolved by amendment — neither attacker landed a blow on the core move, and both
supplied affirmative evidence for it (the exec-codegen round-trip was verified byte-equal by both
sides; `install_strict_arguments` confirmed untouched by Codex).

**Corroborated by both models (highest confidence), all incorporated:**
- The golden pins pydantic titles derived from `__name__` — ten milestone tools use
  underscore-prefixed function names (`_pull_nextArguments`), so `def <tool_name>` codegen fails
  the gate → the `python_name`/wire-name split.
- `outputSchema` derives from the return annotation and the golden never recorded it — the
  acceptance gate was blind to the likeliest regression → mandatory return annotation + extended
  baseline.
- The CLI/tool naming derivation was wrong for 43 of 68 tools → explicit `cli=`/`tool=`, no
  derivation.
- The derivation table missed `float`, `nargs`, `dest`, required collections → extended table +
  declaration-time refusal of unknown shapes.
- "Auto" smoke tests had no executable data model → declared smoke cases.
- The parity ratchet could not distinguish generated from manual → Surface-owned provenance +
  shrinking allowlist.

**Codex uniquely caught** (the cross-model pass's value): generation guarantees surface→op but
not op→domain, so the per-domain forwarding tests must be retained (the review's single best
finding); the `pre=`/`connect=` seam necessity; argparse `type=json.loads` exits 2 not 1; the
frozen `register_tools`/`register_cli` module facade; the capability-matrix and staged-ratchet
requirements; the second defect in the CB-64 crash; `blocker_count` invisible in table render.

**Opus uniquely caught**: the `--json` flag collision with similarity/claims; the sort_keys
renderer mismatch; inverted-boolean help inversion; the `--limit 0` CB-25-class regression the
draft had praised as an improvement; the stderr-contract erasure; the explicit-empty-list
semantic; the claims remainder over-scope (who-holds/list lack the contention contract today);
the ~17-parameter missing population; the 3.11/3.12 dedent discovery.

**Dismissed / rescoped**: the wrapper-only-validation finding was ruled evidence FOR the design
(op bodies are the missing home; the concrete bench bugs filed separately); the deferred-path N+1
is pre-existing behavior preserved by the migration contract (own card); the free-`--json`
whole-CLI-crash was downgraded (per-subparser scoping) with its substance kept.

**Side cards filed from this review**: CB-64 (claims table crash) and the bench XOR/`csv_data=""`
dispatch bugs, the missing `*` in `blockers.blocker_counts_for`, the deferred-path N+1, and the
golden's Python-version dedent exposure.
