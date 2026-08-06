# Checkpoint — Round 1 → Round 2

**Round:** 1
**Date:** 2026-08-06

## User decision (verbatim)

Asked: "Насколько добротно надо записывать владельца, чтобы вопрос «кто держит?» был полезен?"
Answered: **"Настоящее поле — строим B1"**

Preceding clarification from the user, verbatim:
> "Я не понял вопроса. 'Кто держит сущность' может быть достаточным на этом этапе, это кажется
> верным."

## Interpretation recorded for the audit trail

The Judge recommended building almost nothing (~75 lines: `expected_status` + `changed` +
`--append-note`), on the proportionality argument that no consumer for an ownership record was
demonstrated. The Judge explicitly named the condition under which that argument collapses: a real
consumer the council never surfaced.

The user's answer supplies it — **the consumer is the agent (or the user) asking the tracker
"is this taken?" before starting work.** "Кто держит сущность" is accepted as a sufficient goal in
its own right at this stage. The proportionality argument is therefore **overridden by the user**,
knowingly and on the record. The Judge's recommendation is NOT adopted; B1 (Claim Ledger) is.

The Judge's factual findings are NOT overridden and carry into Round 2 unchanged — in particular
that the recommended change would have prevented neither CB-2431 nor CB-2534, and that the binding
constraint at the live call site is signal precision (~41 stale `in_progress` cards), not atomicity.

## Rulings carried into Round 2

- **Requirements projection is per-kind optional — SETTLED.** The Judge ruled that the conjunctive
  requirement "claim projects into status AND first delivery covers findings + requirements" was
  manufactured by the brief, not stated by the user. Codex ruled all nine FATAL on this basis; that
  rejection ground does not stand. Requirements get claims WITHOUT a `reqs` CHECK-constraint
  rebuild.
- **`expected_status` / `changed` is retained**, not as a substitute for B1 but as the direct answer
  to the user's original sentence ("узнать, был ли статус изменен, или это был no-op"). It is
  orthogonal to the ownership record and cheap.
- **Adoption is a first-class deliverable, not a footnote.** `~/.claude/skills/fix-latest-codebugs/
  SKILL.md` is outside this repo and does not count as adoption.

## Orchestrator-verified facts [verified by direct Read this run]

- `worktree-setup.sh:57-66` — CB-2431 (~40 min) and CB-2534 collisions, 2026-08-04, both with the
  card already `in_progress`.
- `tools/CLAUDE.md:10` — user's post-mortem: `in_progress` is "a WRITE-ONLY field that nothing
  reads".
- `worktree-setup.sh:143` — `git -C "${REPO_ROOT}" worktree add …` (the irreversible act).
- `worktree-setup.sh:208-215` — live claim: `codebugs update "${cb}" --status in_progress`, CLI,
  no identity passed; `${BRANCH_NAME}` IS in scope and echoed at `:210`.
- `worktree-setup.sh:114-128` — `open` arm claims; `in_progress` arm warns and never claims.
  Comment at `:120-123`: "~41 cards sit in_progress and a known share of those are stale".
- `reqs.py:22` — requirements CHECK excludes `in_progress`.
- `findings.py:241,270-273` — `append_note` exists in `update_finding`; CLI `update` subparser
  (`findings.py:972-975`) exposes only `--status` and `--notes`.
- `entities.py:20` + `tests/test_entities.py:149-152` — `_SAFE_IDENT` is a test-enforced invariant,
  not a runtime guard. `blockers.py:436-439` already interpolates registry identifiers with a
  `noqa`.
- `db.py:49-59` raises on duplicate `register_schema`; `db.py:178-186`'s docstring claims hook
  naming "matches register_schema discipline" while the hook silently returns — the docstring is
  wrong about its own neighbour.
- C9 probe re-run by orchestrator: 500k rows / 200 actors → anti-join 2.5 ms vs window fold 636 ms;
  8 actors → 59 ms. Cost scales with the queried actor's lifetime claim count.

## Scan attestation

7-point pre-finalization scan: NOT YET RUN — Round 2 pending.
