# `entity_claims` — Implementation Plan

**Companion to `FINAL-DESIGN.md`.** That document is the specification; this one is the order of
operations. Section references (§n) point into `FINAL-DESIGN.md`.

**Date:** 2026-08-06

---

## 1. Files to Create / Modify

### 1.1 `/home/faxik/w/codebugs` — create

| Path | What it is |
|---|---|
| `src/codebugs/claims.py` | **NEW domain module, ~200 lines.** `CLAIMS_SCHEMA` + `ensure_schema`; core layer `_claim_core` / `_release_core` (never commit, never open a transaction); public layer `claim` / `release` (`with db.txn(conn)`, contention-classified); read layer `who_holds` / `held_by` / `list_claims`; helpers `_live_claim`, `_response`, `_elapsed`, `_is_contention`, `_undetermined`, `_next_claim_id`; the terminal hook `_auto_release_on_terminal`; `register_tools` (5 MCP tools) and `register_cli` (4 verbs). Four module-level registrations: `register_schema`, `register_tool_provider`, `register_cli_provider`, `register_status_change_hook`. |
| `tests/test_claims.py` | **NEW, ~260 lines.** 29 tests (§3 below). Own fixtures, no shared `conftest.py`. File-based `tmp_path` DBs throughout — every test either races across connections or exercises `db.connect()` discovery, so the in-memory fixture is not applicable anywhere in this file. |

### 1.2 `/home/faxik/w/codebugs` — modify

| Path | Exact change |
|---|---|
| `src/codebugs/db.py` | **(a)** add `conn.execute("PRAGMA busy_timeout=5000")` inside `connect()` (currently `:492-503`, which sets only `journal_mode=WAL` at `:497`). **(b)** add the `txn()` context manager (§5.3), ~25 lines. **(c)** add `register_status_change_hook` / `run_status_change_hooks` (§5.10.1), ~30 lines, placed beside `register_post_add_hook` (`:178-190`) / `run_post_add_hooks` (`:193`) and mirroring their registration, in-transaction and swallow-and-log policies. **(d)** at `:487`, add `claims` to `from codebugs import findings, provenance, reqs, merge, sweep, bench, blockers, milestones  # noqa: F401`. |
| `src/codebugs/entities.py` | **(a)** add `busy_status: str | None = None` as a **trailing, defaulted** field on the frozen `EntityKind` dataclass (`:23-33`). **(b)** set `busy_status="in_progress"` on the `finding` entry of `ENTITY_KINDS` (`:36-55`); the `requirement` entry declares nothing. **(c)** add `EntityRef.set_status` (§5.9), ~18 lines, with `# noqa: S608` on the interpolated statement — same closed-world justification as the existing `_read` interpolation at `:86`. **Do not** touch `_SAFE_IDENT` at `:20`; it is defined and never referenced (verified), and the real guard is the `readable_cols` membership test at `:83-84`. |
| `src/codebugs/findings.py` | Replace the bare `conn.execute(...)` at `:298` with the capture-and-fire block from §5.10.2: read `old_status` from the `SELECT *` row already fetched at `:252`, capture the cursor, compute `changed = status is not None and cur.rowcount == 1 and status != old_status`, call `db.run_status_change_hooks(...)` when true. `conn.commit()` at `:299` is **unchanged**, and so is the function signature and its response dict. `status` is already canonical at that point via `resolve_finding_status` (`:260`). ~8 lines. |
| `src/codebugs/reqs.py` | The identical shape between `:222` (the `UPDATE requirements`) and `:223` (`conn.commit()`), with `resolve_requirement_status` (`:188`) supplying the canonical value and the pre-read row from `:177`. ~8 lines. **`reqs.py:22-23`'s CHECK constraint is NOT touched.** |
| `src/codebugs/server.py` | Add `"claims": "codeclaims"` to `SERVER_NAMES` (`:22-32`). |
| `src/codebugs/cli.py` | Add `"claims"` to the `--mode` `choices` list at `:49` (currently `["findings", "provenance", "reqs", "merge", "sweep", "bench", "blockers", "milestones", "all"]`). |
| `CLAUDE.md` | Five amendments (§5.13): the sanctioned cross-table status **write**; the `RETURNING` rule; the no-plain-`BEGIN` rule with its two known exceptions; a `Claims module` section stating the ambient-transaction invariant; `register_status_change_hook` documented beside `register_post_add_hook`. |

### 1.3 `/home/faxik/w/autosorter/tools` — modify

Both files are in **another repository**. They carry `set -euo pipefail` and every existing `codebugs`
call in them is `if`-guarded or `|| true`-tailed. **Exactly one new call may be fatal.**

| Path | Exact change |
|---|---|
| `worktree-setup.sh` (274 lines) | **S0:** insert the `git branch --merged` filter between `:88` and `:90` (§6.1, +12). **S1(a):** insert `_claim_one`, `_claim_gate`, `_release_claims_on_abort` and `trap … EXIT` after `:81`, before the loop at `:82` (§6.2(a)). **S1(b):** replace `:107-135` (the `# Registry check + claim` comment through its closing `fi`) with a comment plus `_claim_gate "${cb}"` (§6.2(b)). **S1(c):** insert the `--items` normalization and its claim loop between `:136` (`done`) and `:139` (`mkdir -p`) (§6.2(c)). **S1(d):** insert `trap - EXIT` immediately after `:143` (§6.2(d)). **S1(e):** delete `:197-215` inclusive — the write-only projection loop and its comment block (§6.2(e)). The `--items` milestone-marking block (`if [[ -n "${ITEMS}" ]]` at `:220`, `fi` at `:233`) is **unchanged and stays where it is.** |
| `worktree-finish.sh` (1377 lines) | **S2:** insert the `[7f/9]` release block between `:1333` (the `fi` closing `[7e/9]`) and `:1334` (blank, before `# 8. Clean up worktree` at `:1335`) (§6.3, +38). Uses `${BRANCH}` (`:647`) — **`BRANCH_NAME` does not exist in this file, grep count 0.** Not gated on `SKIP_CHECKS` (`:578`/`:584`). |

**Apply the shell edits bottom-up** (highest line number first) so that earlier anchors stay valid
while later ones are being spliced.

---

## 2. Implementation Steps

Dependency-ordered. Each numbered step is one commit. **Step 1 is independent of every other step and
should not wait on anything.**

### Phase 0 — the change that stands alone

- [ ] **1. S0 — `worktree-setup.sh` ancestry filter** (§6.1). Pure git, no `codebugs` call, +12 lines,
      no deletions. **Depends on nothing; can land before, after, or entirely without the rest of this
      plan.** Verify with `bash -n` and shell check S-a. *If the ledger is cancelled tomorrow, this
      still ships.*

### Phase 1 — infrastructure in `codebugs` (behaviour-neutral)

- [ ] **2. `db.connect()`: explicit `PRAGMA busy_timeout=5000`.** One line, its own commit. Documents
      the 5000 ms currently *inherited* from `sqlite3.connect(timeout=5.0)`'s default. Behaviour-neutral.
      Depends on nothing.
- [ ] **3. `db.txn()` + the no-plain-`BEGIN` ratchet test** (test 24). Depends on step 2. The ratchet
      starts as an allowlist of `db.txn` plus the two known executable sites, `merge.py:242` and
      `milestones/capacity.py:182`, and can only shrink.
- [ ] **4. `entities.py`: `busy_status` field + `EntityRef.set_status` + the precondition test**
      (test 5, both parts). Depends on nothing. Verify `tests/test_entities.py` still passes unmodified
      — the new field is trailing and defaulted, so it must.
- [ ] **5. `db.register_status_change_hook` / `run_status_change_hooks`.** Depends on nothing. Registry
      is empty until step 7, so this is inert on landing.
- [ ] **6. `findings.py` + `reqs.py` fire the hook, guarded by `changed`** (§5.10.2, tests 18 and 17).
      Depends on step 5. This is the only step in Phase 1 that touches a hot path; it fires against an
      empty registry until step 7 lands.

### Phase 2 — the module

- [ ] **7. `claims.py` + `tests/test_claims.py` + wiring** (`db.py:487`, `server.py`, `cli.py:49`).
      Depends on steps 3, 4, 5. This is the bulk of the work. Gates **G1, G2, G6, G7, G8, G9** are
      decided here.
- [ ] **8. CLAUDE.md amendments** (§5.13, five items). Depends on step 7. Do not defer these — each is
      otherwise undiscoverable, and the ambient-transaction invariant in particular is the reason the
      public layer is correct.

### Phase 3 — shell adoption (another repository)

- [ ] **9. S1 — `worktree-setup.sh` claim gate** (§6.2(a)–(e)). Depends on step 7 being installed and
      on `codebugs claim` being on `PATH`. **Apply the five hunks bottom-up.** `bash -n` before
      committing. Gates **G3, G4**.
- [ ] **10. S2 — `worktree-finish.sh` unconditional release** (§6.3). Depends on steps 7 and 9.
      `bash -n` before committing. Gates **G3, G5**.

### Phase 4 — record the deferrals

- [ ] **11. File D1–D13 as codebugs**, one line of rationale each (§10), so the deferral is a record
      rather than an omission.

### Before any of this

- [ ] **Run the validation measurement** (§9): two concurrent `worktree-setup.sh` invocations for one
      card with different slugs; record whether both create worktrees. **This is a measurement, not a
      go/no-go** — the build decision is settled. Record the result in step 7's commit message either
      way.
- [ ] **Answer §8 Q2** — does `pull_next` integration (D1) move back into v1? If yes, this plan grows a
      Phase 2b and D1 returns with a full correctness pass. If no, risk 1 (single consumer, in another
      repo) is accepted knowingly.

---

## 3. Testing Strategy

`tests/test_claims.py`, own fixtures, no shared `conftest.py`, per CLAUDE.md.

### 3.1 Fixtures and precedents

Both concurrency precedents were opened and confirmed this run.

| Precedent | What it gives |
|---|---|
| **`tests/test_milestones.py:801`** — `TestPullNextConcurrent::test_two_threads_two_connections_no_double_claim` (`:802`) | The production shape: **two threads, each opening its own `db.connect(tmp_project)`**, synchronised by `threading.Barrier(2)` at `:817`, uniqueness asserted at `:844` (`assert len(refs) == len(set(refs))`) and coverage at `:846`. **Test 1 follows this shape** and strengthens it: the precedents assert uniqueness only, while test 1 additionally asserts *the loser's outcome string* and *that the loser's response names the winner's full holder triple*. Asserting the loser's report is the entire point of the feature. |
| **`tests/test_sweep.py:754`** — `TestConcurrentAdd::test_concurrent_upsert_atomic` (`:758`) | The N-thread stress shape: `N = 10` (`:771`), `threading.Barrier(N)` (`:773`), raw `sqlite3.connect(db_path, timeout=10.0)` per worker (`:777`), asserting no errors (`:792`), exactly one row (`:795`) and `recurrence_count == N` (`:796-798`). **Test 3 borrows this shape** for idempotence under load. |

Fixtures copy `tests/test_milestones.py:12-22` in shape: `tmp_project` calls `db.init_project(str(tmp_path))`
and **returns `str(tmp_path)`** (it discards `init_project`'s return value); `conn` yields
`db.connect(tmp_project)` and closes it. **File-based, not in-memory** — every claims test either races
across connections or exercises `db.connect()` discovery.

### 3.2 The 29 tests

**Tests 1–9 prove the success criteria.**

| # | Test | Proves |
|---|---|---|
| 1 | 2 threads / 2 connections / barrier → exactly one `claimed`; **the loser's outcome is `held_by_other` and its response names the winner's full holder triple** | Mutual exclusion. **Deploy gate G1.** |
| 2 | `utc_now` monkeypatched to a constant; two `claim()` calls, same triple → `claimed` then `already_mine`, `touch_count` 1 → 2 | Idempotence. **Fails on any timestamp-based discriminator**; passes on `touch_count` |
| 3 | 10 threads claiming as the *same* holder (sweep shape) → 1 `claimed` + 9 `already_mine`, final `touch_count == 10`, exactly one live row | Idempotence under load |
| 4 | `who_holds` / `held_by` return the right rows; `EXPLAIN QUERY PLAN` on both shows `USING INDEX idx_claims_live` / `idx_claims_holder_live` | The ownership query, **and** that it stays a point query rather than a fold |
| 5 | **(a)** A synthetic third `EntityKind` declared in the test with `busy_status='working'` over a purpose-built table: claim → project → release, with **zero changes to `claims.py`**. **(b)** For every kind in `ENTITY_KINDS` with `busy_status is not None`: `PRAGMA table_info` contains `{id,status,updated_at}` (P1); a `set_status` to the declared value round-trips byte-identically, then rolls back (P2); `busy_status not in kind.terminal` (P3) | A third kind by declaration, **and** §5.9's preconditions enforced rather than assumed. Both parts are one test |
| 6 | Full claim → release lifecycle on `FR-1`: `projected == false`, `prev_status`/`projected_to` both NULL, requirement status unchanged throughout, **no CHECK violation** | The user's ratified decision |
| 7 | `findings.query_findings(status="in_progress")` still returns claimed findings after a projecting claim | No regression on the existing read |
| 8 | `idle_seconds` grows with a frozen-then-advanced clock; **no `stale`, `orphaned` or `divergent` key appears on any returned row**; `list_claims` never returns a released row | Staleness *reporting* at its real strength, and that the deferred audit surfaces did not reappear |
| 9 | `tests/test_milestones.py:801-846` passes **unmodified**; `pull_next` behaviour is byte-identical to today | That the deferral is real. **Deploy gate G9** |

**Tests 10–29 exist because a specific defect was found.**

| # | Test | Defect it locks down |
|---|---|---|
| 10 | Open a transaction, call every `_*_core`, assert `conn.in_transaction` is still `True` afterwards | A core function that commits fails here |
| 11 | `with db.txn(conn): with db.txn(conn):` — inner yields `False`, no `OperationalError`, exactly one commit | `db.txn` reentrancy |
| 12 | Force an exception inside `db.txn` after SQLite auto-rolled back; assert the **original** exception surfaces, not `cannot rollback - no transaction is active` | The guarded `ROLLBACK` |
| 13 | `busy_timeout=0` + a write lock held by a second connection → `claim()` returns `outcome="undetermined"`, **no exception escapes** | Without this the fourth outcome is documentation |
| 14 | Inject a non-contention `OperationalError` (`sqlite_errorcode = 1`) → it **propagates** and is not reported as `undetermined` | The classifier, other direction |
| 15 | Claim `FR-1` while its status is `implemented` → `entity_terminal`. Repeat with `project=False` on a `fixed` CB → also `entity_terminal` | The terminal guard, which Round 2 made unreachable for every requirement and every `--no-project` claim |
| 16 | Claim CB-1, `update_finding(status="fixed")` → auto-released, and the row is **still there** with `released_at` set and `release_reason='terminal:fixed'` | The terminal hook, and that history has a home |
| 17 | Claim `FR-1`, `update_requirement(status="implemented")` → auto-released with `release_reason='terminal:implemented'` | **Both** writers fire the hook, not just findings |
| 18 | Spy hook counting invocations: `update_finding(status="fixed")` on a finding already `fixed` fires **zero** hooks; `update_finding(notes=…)` with no status fires zero; a real `open→fixed` fires one | The `changed` guard, all three arms |
| 19 | Spy hook: a claim followed by a release fires **zero** status-change hooks | The no-recursion invariant |
| 20 | Claim as `(br-a, branch, /repo/x)`; re-claim as `(br-a, branch, /repo/y)` → `held_by_other`, response names `/repo/x`. Then `(br-a, agent, /repo/x)` → `held_by_other`. Then a `holder_repo=None` claim renewed with `None` → `already_mine` | Full-triple identity on **claim**, all three arms including the NULL case |
| 21 | Claim (projects `open→in_progress`) → holder sets `fixed` → `release()` → status stays `fixed`, `status_restored == false` | Release never resurrects finished work |
| 22 | Claim → release → **claim by a different holder** → succeeds with a new `claim_id`; the old row survives with `released_at` set; live count is 1 at every step | Soft delete; no release/reclaim race |
| 23 | Release with a holder that does not hold it → `not_yours` naming the real holder; release when nothing is claimed → `not_claimed`; **neither writes anything** | The two release misses |
| 24 | Source-tree ratchet: `grep -rn 'BEGIN' src/codebugs/` yields only `db.txn` plus the two known pre-existing executable sites (`merge.py:242`, `milestones/capacity.py:182`) | The no-plain-`BEGIN` rule, ratcheted so the deferred refactor can only shrink the allowlist. **Deploy gate G6** |
| 25 | CLI exit codes via `subprocess`: `claim` twice with different holders → `0` then `3`; on a `fixed` finding → `4`; **`claim` against a DB whose write lock is held with `busy_timeout=0` → `5`**; **`release` in the same condition → `5`**; `who-holds` on an unheld id → `3`; `claims --format ids` with no matches → **rc 0 and empty stdout** | The shell contract. **An unasserted exit code is an unasserted gate**, and §6.2(a)'s retry is dead code if 5 is never emitted. **Deploy gates G2, G7** |
| 26 | `claims --format ids` output is bare ids, one per line, no header, and round-trips into `release` | §6.3's loop, which is only correct if this holds. **Deploy gate G7** |
| 27 | **Ambient-transaction invariant.** Open a transaction on a connection, then call the public `claim()` on that same connection, and assert the documented v1 contract: the public layer is only correct on a clean connection. Assert that `server.py`'s `_conn` and the CLI both hand out fresh connections (`conn.in_transaction is False` at entry) | On an implicitly-opened transaction, `db.txn` yields `False`, the write happens, **nothing commits**, and `claim` still returns `claimed` — reproduced against the real module. Unreachable in v1 only because every public caller holds a fresh connection; this test is what keeps that true |
| 28 | **Response-builder completeness.** Drive all nine outcomes (`claimed`, `already_mine`, `held_by_other`, `entity_terminal`, `undetermined`; `released`, `not_yours`, `not_claimed`, `undetermined`) and assert every response dict contains **all fifteen** `_COMMON_KEYS` | §5.6 — Round 3 promised this and returned partial dicts from two paths |
| 29 | **Release authorization on the full triple.** Claim as `(br-a, branch, /repo/x)`. Release as `(br-a, branch, /repo/y)` → `not_yours`, **claim still live**. Release as `(br-a, agent, /repo/x)` → `not_yours`, still live. Release as `(br-a, branch, NULL)` → `not_yours`, still live. Release as the exact triple → `released`. A `holder_repo=NULL` claim released with `NULL` → `released` | The hole the Codex verifier found: with `holder`-only matching, a same-text holder of another kind or repo could release someone else's claim |

**Definition of done for the module: all 29 tests pass**, plus `uv run python -m pytest tests/ -v`
green with no regressions (specifically including `tests/test_milestones.py:801-846` **unmodified**),
plus `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.

**A green suite is not a deploy gate.** See §9 of `FINAL-DESIGN.md` — G1–G9 are the gates, and four of
them (G3, G4, G5, G8) cannot be satisfied by pytest at all.

### 3.3 Shell checks

The shell diffs are not covered by pytest. Three scripted checks, run against a throwaway clone before
merge.

| # | Check | Passes if |
|---|---|---|
| **S-a** | `AUTOSORTER_SETUP_NO_CLAIM=1 worktree-setup.sh …` on a card that has an existing **merged** branch | S0's filter drops it, setup proceeds, and **no `codebugs` write occurs at all** (which is only true after S1(e) deletes the projection loop) |
| **S-b** | `worktree-setup.sh` for a two-card branch name where the **second** card is claimed by another holder | rc 1; **no worktree created, no branch created**; the **first** card's claim released by the EXIT trap. **Mandatory — deploy gate G4.** It is the only end-to-end proof that the gate aborts before `git worktree add` and that the trap covers an early `exit` |
| **S-c** | setup → finish round trip | the claim is created against the **main repo's** `.codebugs/` and `[7f/9]` releases it (or reports "nothing held" because the terminal hook already did). **Deploy gate G5** |

Additionally, before either shell commit: splice the diffs into copies of the real files bottom-up and
run **`bash -n`** on both (deploy gate G3). The `:197-215` deletion in particular must leave `:195`
(`fi`) → `:196` (blank) → the `--items` comment, with no orphaned `fi`/`done`.

---

## 4. Rollback Plan

The delivery is built so that every layer can be reverted independently, and so that reverting the
whole thing leaves no residue in the two tables that matter.

### 4.1 Full rollback

```sql
DROP TABLE entity_claims;    -- takes idx_claims_live / idx_claims_holder_live / idx_claims_entity with it
```

plus `git revert` of steps 2–10. **`findings` and `requirements` are left exactly as they were**,
because the only thing claims ever wrote into them is a `status` value they were always allowed to
hold (`in_progress` for findings; nothing at all for requirements). No migration to undo, no CHECK
constraint rebuilt, no column added to an existing table.

The one artefact that survives a rollback: findings that were projected to `in_progress` and never
released stay `in_progress`. That is the same state the *current* script already leaves them in
(`worktree-setup.sh:208-214` does exactly this today), so rollback returns the system to its present
behaviour rather than to a novel one.

### 4.2 Partial rollback, by layer

| Revert | Effect | Safe alone? |
|---|---|---|
| **S2 only** (`worktree-finish.sh`) | Claims are no longer released at merge time by the belt. The terminal hook still releases every claim whose finding gets a `Fixes:` trailer. Leaked claims become visible via `codebugs claims` and are released by hand. | **Yes.** |
| **S1 only** (`worktree-setup.sh`) | No claims are taken by the branch workflow; the ledger goes quiet. **Restore `:197-215`** (the projection loop) in the same revert, or findings stop being flipped to `in_progress` at all. S2 then finds nothing to release and prints "nothing held". | **Yes, if the projection loop is restored in the same commit.** |
| **S0 only** | The guard goes back to refusing on merged-but-undeleted branches. Independent of everything else. | **Yes.** |
| **Step 7 (`claims.py`)** | Requires reverting S1 and S2 first — they call `codebugs claim` / `release` / `claims`. If the module vanishes while the scripts remain, the calls fail: setup's gate is `if command -v codebugs`-guarded and rc-captured, so it lands in the `*)` arm and proceeds unclaimed; finish's block is `if`-guarded and `|| true`-tailed. **Neither script dies** — but the state is confusing and should not be left in place. | Revert the shell first. |
| **Step 6 (hook firing)** | Terminal auto-release stops. Claims must then be released explicitly (S2 still does this for merged branches). Requires no other revert. | **Yes.** |
| **Steps 2–5** (`busy_timeout`, `db.txn`, hook registry, `entities.py`) | Each is behaviour-neutral on its own and each is a separate commit. Reverting `db.txn` or `entities.py` requires reverting step 7 first. | Reverse order. |

### 4.3 The abort paths, which are rollback in miniature

- **Setup fails before `git worktree add`:** the `EXIT` trap releases every claim taken so far
  (§6.2(a)). Nothing irreversible has happened; the card is free for the next run.
- **Setup fails after `git worktree add`:** the trap is disarmed (§6.2(d)) and the claim stays. This is
  deliberate — a real worktree is on disk. Recover with
  `codebugs release <id> --holder <branch> --holder-kind branch --repo <repo>`.
- **A claim is stuck for any other reason:** `codebugs who-holds <id>` names the holder, its kind and
  its repo; `codebugs release` with that exact triple clears it. There is no state a single manual
  release cannot recover.
