# Architect B — Round 3: `entity_claims`, the final implementable design

**Round:** 3 (targeted fix pass, not a redesign)
**Date:** 2026-08-06
**Status:** implementable. An implementer needs this document and nothing else from Rounds 1–2.

This document supersedes `07-architect-r2.md` in full. Where the two disagree, this one is correct;
§2 lists every Round-2 claim I am retracting, including one where I accused the rest of the council
of a citation error that was mine.

---

## 0. Verification ledger — what I opened or executed THIS run

Every line number below was produced this session by `awk 'NR>=X&&NR<=Y{printf "%d: %s\n",NR,$0}'`
against the absolute path shown, or by a command whose output I read. Nothing is carried forward on
trust from Round 2.

### 0.1 `/home/faxik/w/autosorter/tools/worktree-setup.sh` — **274 lines**

| Anchor | Content (verbatim, opened this run) |
|---|---|
| `:8` | `set -euo pipefail` |
| `:14` | `REPO_ROOT="$(dirname "${_GIT_COMMON}")"` |
| `:17` | `ITEMS=""` |
| `:22` | `--items=*) ITEMS="${arg#--items=}" ;;` |
| `:38-39` | `BRANCH_NAME="$1"` / `BASE_BRANCH="${2:-HEAD}"` |
| `:42-43` | `SLUG="${BRANCH_NAME//\//-}"` / `WORKTREE_PATH="${WORKTREE_DIR}/${SLUG}"` |
| `:75-79` | `CB_IDS=$(printf '%s' "${BRANCH_NAME}" \| grep -oiE 'cb-?[0-9]{3,}' … \|\| true)` |
| `:81` | `_claim_ids=""` |
| `:82` | `for cb in ${CB_IDS}; do` |
| `:83` | `num="${cb#CB-}"` |
| `:86-88` | `others=$(git -C "${REPO_ROOT}" branch --format='%(refname:short)' \| grep -iE … \|\| true)` — **the read** |
| `:90-105` | `if [[ -n "${others}" ]]` … `exit 1` … `fi` |
| `:107-110` | comment `# Registry check + claim. Best-effort, …` |
| `:111` | `if command -v codebugs >/dev/null 2>&1 && [[ -z "${AUTOSORTER_SETUP_NO_CLAIM:-}" ]]; then` |
| `:112-113` | `status=$(codebugs get "${cb}" 2>/dev/null \| sed -nE …)` — the check-then-act pre-read |
| `:114-134` | `case "${status}" in` … `esac` (`:120` is the `~41 cards` comment) |
| `:135` | `fi` |
| `:136` | `done` |
| `:139` | `mkdir -p "${WORKTREE_DIR}"` |
| `:143` | `git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${BASE_BRANCH}"` — **the write** |
| `:195` | `fi` (end of the symlink block) |
| `:196` | blank |
| `:197-207` | comment `# Auto-claim the card now that the worktree exists (CB-2489 fix (c)).` |
| `:208-214` | `for cb in ${_claim_ids}; do` … `codebugs update "${cb}" --status in_progress` … `done` |
| `:215` | blank |
| `:216-233` | the `--items` / `milestone-mark-branch` block |
| `:258-262` | final success banner (`echo ""`, `=== Worktree ready ===`, path, branch, blank) |

`grep -n 'trap ' worktree-setup.sh` → **no match**. `grep -n 'flock'` → **no match**.

### 0.2 `/home/faxik/w/autosorter/tools/worktree-finish.sh` — **1377 lines**

| Anchor | Content |
|---|---|
| `:11` | `set -euo pipefail` |
| `:578` / `:584` | `SKIP_CHECKS=false` / `--skip-checks) SKIP_CHECKS=true ;;` |
| `:647` | `BRANCH=$(git -C "${WORKTREE_PATH}" branch --show-current)` — the **only** branch variable |
| `:1198` | `if git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff --no-verify -m "Integrate ${SLUG}" 2>/dev/null; then` |
| `:1241` | `flock -u 9` (integration lock released) |
| `:1249-1257` | `[7b/9]` auto-resolve-codebugs.py, `SKIP_CHECKS`-gated, `\|\| echo "⚠ … (non-fatal)"` |
| `:1262-1270` | `[7c/9]` auto-mark-milestone-integration.py, same shape |
| `:1288-…` | `[7d/9]` auto-resolve-autosorter-bugs.py, same shape |
| `:1322-1333` | `[7e/9]` regen-milestone-rollup.py, same shape; `fi` at `:1333` |
| `:1334` | blank |
| `:1335-1337` | `# 8. Clean up worktree` / `echo ""` / `echo "[8/9] Cleaning up worktree..."` |
| `:1338` | `git -C "${REPO_ROOT}" worktree remove "${WORKTREE_PATH}"` |

`grep -c 'BRANCH_NAME' worktree-finish.sh` → **0**.
`grep -n 'trap ' worktree-finish.sh` → **no match**.
`grep -n 'branch -d\|branch -D\|branch --delete'` → **no match**. Branches are never deleted.

### 0.3 `/home/faxik/w/codebugs/src/codebugs/` — opened this run

| Fact | Anchor |
|---|---|
| `db.connect()` sets `journal_mode=WAL` and **nothing else** | `db.py:492-503`, PRAGMA at `:497` |
| `_ensure_modules_loaded()` import list | `db.py:478-489`, the `from codebugs import …` at `:487` |
| `register_post_add_hook` — the seam this design mirrors | `db.py:178-190`; runner `run_post_add_hooks` at `:193` |
| `git_rev_parse` runs `["git","rev-parse",ref]` — **no `--verify`** | `db.py:210-226` |
| `row_to_dict` is **public** | `db.py:229` |
| `findings.update_finding`: pre-read `SELECT *` | `findings.py:252` |
| … the `UPDATE findings SET …` | `findings.py:298` |
| … `conn.commit()` — **this is why projection cannot route through it** | `findings.py:299` |
| `findings._next_id` — the id-generation shape `CLM-<n>` copies | `findings.py:117` |
| `reqs.update_requirement`: pre-read / UPDATE / commit | `reqs.py:177` / `:222` / `:223` |
| `requirements` status CHECK constraint — **not touched by this design** | `reqs.py:22-23` |
| `entities.py` is **113 lines**; `EntityKind` frozen dataclass | `entities.py:23-33` |
| `ENTITY_KINDS` tuple (2 kinds) | `entities.py:36-55` |
| `EntityRef._read` — the one interpolated cross-table read, `# noqa: S608` | `entities.py:80-89`, noqa at `:86` |
| `EntityRef.status` / `.exists` / `.require` / `.is_resolved` | `entities.py:94-95` / `:91-92` / `:105-108` / `:100-103` |
| `_SAFE_IDENT` is **defined at `:20` and never referenced** — the real guard is `readable_cols` at `:83-84` | `entities.py:20`, `:83-84` |
| `types.utc_now()` formats to whole seconds | `types.py:12-14` |
| `FINDING_TERMINAL` / `REQUIREMENT_TERMINAL` | `types.py:36` / `types.py:41` |
| `cli.py` `--mode` choices list | `cli.py:49` |
| `server.py` `SERVER_NAMES` | `server.py:22-32` |
| `milestones` is a **package**, not `milestones.py` | `src/codebugs/milestones/__init__.py` |
| Test precedent: 2 threads / 2 `db.connect` / `threading.Barrier(2)` | `tests/test_milestones.py:801-846` |
| Test precedent: 10 threads / raw `sqlite3.connect(timeout=10.0)` / barrier | `tests/test_sweep.py:754-799` |
| Fixtures: `tmp_project` = `db.init_project(tmp_path)`; `conn` = `db.connect(tmp_project)` | `tests/test_milestones.py:12-22` |
| SQLite in the project venv / system | **3.47.1** (`uv run python`) / 3.46.1 (`python3`) |

### 0.4 Probes executed this run

**P1 — NULL-safe holder-triple identity (the M3 fix).** `uv run python`, SQLite 3.47.1, real table
with the partial unique index, upsert whose `DO UPDATE … WHERE` compares
`holder = excluded.holder AND holder_kind = excluded.holder_kind AND holder_repo IS excluded.holder_repo`:

| Case | `RETURNING` row |
|---|---|
| fresh `(br-a, branch, /repo/x)` | `('CLM-1','br-a','branch','/repo/x', touch=1, was_new=1)` |
| same triple again | `('CLM-1','br-a','branch','/repo/x', touch=2, was_new=0)` |
| same holder, **different repo** `/repo/y` | **`None`** |
| same holder+repo, **different holder_kind** `agent` | **`None`** |
| same holder, **`holder_repo=NULL`** | **`None`** |
| fresh with `holder_repo=NULL`, then repeated | `touch=1` then `touch=2` — `IS` matches NULL to NULL |
| live rows after all of it | **1** |

M3 is closed by execution, including the NULL case that a naive `=` comparison would silently fail.

**P2 — shell constructs in the diffs of §15.** All run with `bash -c 'set -euo pipefail; …'`:

| Probe | Result |
|---|---|
| `_rc=0; (exit 3) \|\| _rc=$?; case "$_rc" in 3) …` | `REACHED rc=3`, then `SURVIVED` — rc capture works under `set -e` |
| `_h=$(echo hi \| grep -oE zzz \|\| true)` | `SURVIVED empty=[]` |
| same **without** `\|\| true` | rc=1, script died — this is defect S2 reproduced |
| `_h=""; for x in ${_h}; do …; done` | `SURVIVED_ZERO_ITER` — safe under `set -u` |
| `trap "…" EXIT; trap "…" ERR; exit 1` | `EXIT_TRAP_FIRED` printed, `ERR_TRAP_FIRED` **not** — defect S4 reproduced |
| `trap "…" EXIT; trap - EXIT; exit 0` | disarm works, nothing fired |
| `grep -vxF -f <(printf …) \|\| true` on partial and total match | `remaining=[fix-cb-1-b]` and `allmerged=[]`, both survived |

---

## 1. Why this ships — the justification, restated at its real size

Round 2 argued the ledger earns its place because it makes ownership *trustworthy*, via a git
liveness predicate, and because a released claim is "the one thing that can tell a stale branch from
work in flight". **Both halves are dead.** What remains is smaller and it is the only thing I claim.

### 1.1 The one thing the ledger does that nothing else does: the atomic gate

`worktree-setup.sh` is check-then-act. The ref-namespace **read** is at `:86-88`; the ref-creating
**write** is at `:143`. Between them sit a `git branch` invocation, a `codebugs get` Python
subprocess (`:112`, realistically 100–500 ms of interpreter startup), a `case`, and a `mkdir`.
Nothing serializes that window — `grep -n flock worktree-setup.sh` → no match. Two setups launched
concurrently for the same card **with different slugs** both observe `others=""` and both proceed,
and git cannot rescue it because the two branch names differ by construction. That is the entire
premise of the bug: `git worktree add -b` has no ref collision to detect.

A unique partial index on `entity_claims(entity_id) WHERE released_at IS NULL`, written inside a
`BEGIN IMMEDIATE` **before** `:143`, makes those two claims mutually exclusive at the database. One
returns `claimed`; the other returns `held_by_other` and names the winner. This requires **zero
history** — it works against an empty table on the first run.

**That is the whole justification. There is no second one.**

### 1.2 The predicate that replaced git liveness — ancestry, not existence

Round 2's L3 said a branch that exists is "provably still checked out somewhere". It is not:

- `worktree-finish.sh` contains **no** `git branch -d/-D` anywhere (grepped this run, 0 hits). It
  removes the worktree at `:1338` and leaves the ref. So a branch-existence check returns `live` for
  every branch ever created, forever. It discriminates nothing in the only deployment that would use it.
- The predicate is also wrong *as written*, not merely as implemented. `db.git_rev_parse`
  (`db.py:210-226`) runs `git rev-parse <ref>` with no `--verify` — but adding `--verify` does not
  fix it either, because `git rev-parse --verify refs/heads/main~1` **returns a SHA**. `--verify`
  guarantees a single revision, not an exact ref. The correct existence primitive is
  `git show-ref --verify --quiet "refs/heads/${b}"`.

The correct predicate is **ancestry**, not existence. Integration is `merge --no-ff`
(`worktree-finish.sh:1198`; no `--squash` in the file), so every integrated branch is a strict
ancestor of the integration branch and `git branch --merged` / `git merge-base --is-ancestor`
separates merged branches from work in flight exactly, with zero new state.

### 1.3 The consequence I am not hiding

**Once ancestry is the predicate, the same one-line filter inside the guard loop at `:86-88` closes
the merged-but-undeleted false-positive class *without the ledger*.** No table, no module, no
history, no exit-code contract, effective on the first run. Round 2's central sentence — *"A claim
record that is released at merge time is **the one thing** that can tell a stale branch from work in
flight"* — is simply false, and I am deleting it rather than repairing it.

That filter is specified in §15.1 as **commit S0**, and it is deliberately structured to land
independently of everything else in this design. If the ledger were cancelled tomorrow, S0 should
still ship. It is strictly better than the ledger at the job the ledger was sold on.

### 1.4 What the evidence actually supports — stated at its real strength

Both incidents on record are **sequential, not concurrent**. `worktree-setup.sh:58-66` records
CB-2431 duplicated *"for ~40 minutes"* and CB-2534's two slugs as *"built in parallel on
2026-08-04"* — where "in parallel" describes parallel *work*, not two `worktree-setup.sh`
invocations overlapping inside the sub-second `:86`→`:143` window. **The shipped guard already
refuses both of those.** Nobody in this council — not me, not either adversary, not the Judge — has
established that either observed incident was a race.

So: **the ledger prevents a real but so-far-unobserved subclass.** The mechanism is sound and proven
on real SQL by two model families. The *incidence* is unmeasured. I am not going to dress that up.
The honest version of the pitch is:

> The tracker currently has no way to answer "is this taken?" — `findings.status='in_progress'` is a
> write-only field with ~41 stale rows, read by nothing that gates. This adds a queryable ownership
> record with an atomic gate. The gate closes a TOCTOU window that provably exists in the shipped
> script and that has not yet been observed to fire.

The queryable ownership record — success criterion 3, "what does agent-7 hold" as an indexed point
query — is what the user approved in Round 1 (`CHECKPOINT-r1:24`, *"the agent (or the user) asking
the tracker 'is this taken?' before starting work"*). That is the deliverable. The gate is the part
that makes the record binding instead of advisory.

### 1.5 A ten-minute test that should be run before this is load-bearing

The Judge's second hesitation is right and I am carrying it forward as a task, not a footnote: launch
two `worktree-setup.sh` invocations concurrently for one card with different slugs and observe
whether both create worktrees. If the window turns out to be practically unreachable in this
workflow, §1.1's justification thins further and the user should hear it **before** implementation.
This is listed as a pre-implementation task in §20.

---

## 2. Retractions — every Round-2 claim now known false

Listed so an implementer who read Round 2 knows exactly what to unlearn. Each retraction names who
found it.

| # | Round-2 claim | Status | Found by |
|---|---|---|---|
| R1 | *"The Round-1 line numbers are off by two"* (`07:87-119`) — I accused the whole council of a systematic citation error | **FALSE, and it was mine.** `worktree-setup.sh` is **274** lines (I said 275); `git worktree add` is at `:143` (I said `:141`); the claim loop is `:208-214` (I said `:206-212`); the `~41 cards` comment is at `:120` (I said `:117-125`). I almost certainly read one of the 46 stale copies under `.worktrees/`. Re-verified all four this run. | Codex + Opus + orchestrator, independently |
| R2 | L3: *"A branch that exists is provably still checked out somewhere"* (`07:165-178`) | **FALSE.** No branch deletion exists in `worktree-finish.sh` (0 grep hits). The predicate returns `live` for every branch forever. | Opus + Codex |
| R3 | The liveness predicate is right and only its implementation lacks `--verify` | **FALSE.** `git rev-parse --verify refs/heads/main~1` returns a SHA. `--verify` means "one revision", not "an exact ref". The right primitive is `git show-ref --verify --quiet`. | Judge (neither adversary caught this) |
| R4 | *"CB-2534 is already prevented by shipped code that is not mine"* and *"any claim that this design prevents CB-2534 is claiming credit git already earned"* (`07:223-230`, `07:1312-1315`) | **FALSE — a factual reversal against my own interest, in the wrong direction.** The guard is check-then-act; two concurrent setups with different slugs both pass. The ledger *does* add real prevention. I over-corrected in Round 2 and gave away a true claim. | Codex (Opus and I both had this wrong) |
| R5 | §13.0: *"A claim record released at merge time is **the one thing** that can tell a stale branch from work in flight"* | **FALSE.** `git merge-base --is-ancestor` does it with no new state. Deleted, not repaired. | Opus |
| R6 | §13.2(a)'s `who-holds`-exit-3 false-positive downgrade | **CUT.** Inert on day one (empty table), and its job is done better by ancestry. Also: exit 3 means only "no live row, some history exists" — it does not prove the *observed* branches match the released holder. | Opus (inertness) + Codex (unsound inference) |
| R7 | §13.3's finish-release block using `${BRANCH_NAME}` | **BROKEN.** `BRANCH_NAME` does not exist in `worktree-finish.sh` (grep count 0). Under `set -u` (`:11`) the block aborts with `unbound variable` **after** `merge --no-ff` already landed on main. The variable is `BRANCH` (`:647`). | Codex (probe) |
| R8 | §13.3's *"by the time this block runs it is usually a no-op"* | **The no-op path was the fatal one.** `grep` with no match exits 1; with `\|\| true` misplaced inside the loop body instead of on the pipeline, `set -euo pipefail` kills the script post-merge. Reproduced this run. | Codex (probe) |
| R9 | §13.2(a)'s unguarded `codebugs who-holds` followed by `case $?` | **UNREACHABLE.** `set -e` terminates before `case` runs. Reproduced this run. | Codex (probe) |
| R10 | §13.2(c)'s `trap '_release_claims_on_abort' ERR` installed after the claim loop | **BROKEN twice.** Installed too late to cover early `exit 1`s inside the loop, *and* an explicit `exit` does not fire an `ERR` trap at all. Reproduced this run. | Codex (probe) |
| R11 | §13.2(b)'s outcome-5 retry | **UNSOUND.** The retry tested the second call as a boolean, so outcomes 3, 4, 5 and ordinary error all landed in *"stayed busy; continuing UNCLAIMED"* — a rival winning during the `sleep` produced a loser that proceeded. | Codex |
| R12 | §6.1's `entity_terminal` guard, advertised as the fix for a mandatory defect | **FALSE for requirements.** Gated on `project and busy is not None`, so it never ran for `EntityKind`s that declare no `busy_status` — i.e. every requirement — nor for `--no-project`. | Opus + Codex, independently |
| R13 | §9's terminal hook firing rule | **UNGUARDED.** Fired on "intended status differs from pre-read row", not on the write actually having happened. A refused write could release ownership on an entity whose status never became terminal. | Codex |
| R14 | §6.1's idempotence check (`WHERE entity_claims.holder = excluded.holder`) | **INCOMPLETE.** `holder_kind` and `holder_repo` were neither compared nor refreshed, so identical branch names in two repos were the same claimant. Fixed by the full-triple NULL-safe comparison, executed as P1. | Codex |
| R15 | §11.2's `release_item` integration pseudocode | **CANNOT RUN.** It passes `holder=agent_id`; `release_item`'s variable is `agent` (`milestones/capacity.py:234-235`). Moot — `pull_next`/`release_item` integration is DEFERRED. | Codex |
| R16 | §3.1's *"`claims prune --before <iso>` exists as an explicit CLI verb"* | **PROMISE WITHDRAWN.** It appeared in prose and in no CLI surface. There is no retention verb in v1; see §12.4. | Codex |
| R17 | §10.1's `git_branch_verifier` cwd handling | **INCOMPLETE.** A file-as-cwd probe raised an uncaught `NotADirectoryError` through `db.git_rev_parse`. Moot in v1 — the verifier is DEFERRED and this design makes **no git subprocess call from Python at all**. Carried as a precondition on the deferred audit work (§19). | Codex |
| R18 | §8.1's *"a third kind is one `EntityKind` entry"* | **SYNTACTICALLY TRUE, SEMANTICALLY INCOMPLETE.** It silently assumed every projecting table exposes `id`/`status`/`updated_at`, accepts the declared busy value past its CHECK constraint, and tolerates bypassing its domain-update side effects. Now written down as a contract and enforced by an executed test (§10.3). | Codex |
| R19 | §17's `FATAL-1 … MEDIUM-8` defect ledger | **PROCESS DEFECT, conceded.** That numbering is my own invention; it appears nowhere in Round 1. Opus's *"the defendant wrote the indictment"* is fair. This round uses the Judge's and the adversaries' own labels (S1–S9, M1–M9) and nothing of my own. | Opus |

**Standing corrections from Round 2 that survive** (not retracted, restated because they are load-bearing):

- `entities.py:20`'s `_SAFE_IDENT` is **defined and never referenced**. The real guard on the one
  interpolated identifier is the `readable_cols` membership test at `entities.py:83-84` against the
  frozen `ENTITY_KINDS`. Round 1 (mine and Architect C's) described `_SAFE_IDENT` as the guard.
- Round 1's claim that an `entity_id`-PK table was "a strict column subset" upgradable by
  `ALTER TABLE` was **false** — SQLite cannot drop or replace a primary key without a table rebuild.
  §4 does not need the migration because `claim_id` is the PK from day one.
- CLAUDE.md's `blockers.py` debt bullet cites `db._row_to_dict()` / `reqs._row_to_dict()`. Neither
  exists; it is the public `db.row_to_dict` at `db.py:229`. Unrelated stale fact, filed in §19.

---

## 3. Module boundaries

```
src/codebugs/
  claims.py      NEW domain module — schema, 5 public fns, 5 MCP tools, 4 CLI verbs   ~200 lines
  db.py          + txn()                          reentrant transaction helper (§5)    ~25 lines
                 + register_status_change_hook / run_status_change_hooks (§11)         ~30 lines
                 + PRAGMA busy_timeout=5000 in connect()                                 1 line
  entities.py    + EntityKind.busy_status: str | None = None   (declarative)             1 line
                 + EntityRef.set_status()   the module's FIRST write path              ~18 lines
  findings.py    + fires run_status_change_hooks, conditioned on `changed` (§11.2)      ~8 lines
  reqs.py        + fires run_status_change_hooks, conditioned on `changed` (§11.2)      ~8 lines
  server.py      SERVER_NAMES["claims"] = "codeclaims"                                   1 line
  cli.py         --mode choices += "claims"                                              1 line
  db.py:487      _ensure_modules_loaded() import list += claims                          1 line
tests/
  test_claims.py NEW                                                                  ~250 lines
/home/faxik/w/autosorter/tools/
  worktree-setup.sh   claim gate + abort trap; delete the write-only projection loop   ~30 lines
  worktree-finish.sh  unconditional release                                            ~12 lines
```

### 3.1 The dependency graph, stated as a rule

```
claims.py  ──imports──▶  db, entities, types, fmt
findings.py ──imports──▶  db          (fires db.run_status_change_hooks)
reqs.py     ──imports──▶  db, entities (fires db.run_status_change_hooks)
```

**No domain module imports `claims`, and `claims` imports no domain module.** The direction is
`claims → db ← findings`. `claims.py` registers its terminal hook into `db`'s registry at module
level; `findings.py` and `reqs.py` call the runner without knowing who is listening. This is the
exact shape of the existing `register_post_add_hook` seam (`db.py:178-190`), where
`milestones.auto_route_finding` reaches `findings.add_finding` without `findings` knowing
`milestones` exists.

**M4 is dissolved by the deferral, and I checked rather than assumed.** Round 2 had
`milestones/capacity.py` calling `claims._claim_core` / `_release_core`, which violates CLAUDE.md's
*"They must NOT import each other's private functions — only public interfaces."* With `pull_next`
integration DEFERRED (§19), **no module outside `claims.py` calls a `_*_core` function**. The core
layer survives for one in-module reason only: the terminal hook runs inside `update_finding`'s
already-open transaction and must not commit (§5.2). Private-across-modules is gone; private-within-
module is ordinary.

**Nothing here breaks if the deferral is reversed later** — when `pull_next` integration lands, it
gets a *public* ambient-transaction API (`claims.claim(conn, ..., manage_txn=False)` or a renamed
`claim_within_txn`), decided in that commit, not smuggled in here.

### 3.2 The one CLAUDE.md rule I am changing, explicitly

> `entities.py` becomes read **and write**, gaining exactly one write method, `EntityRef.set_status`.

`entities.py`'s own docstring already claims the role — *"Owns the one sanctioned cross-table read
over `findings` / `requirements`… Adding a new entity kind is a single entry in `ENTITY_KINDS`"*
(`entities.py:4-7`). The only way to keep the second half of that sentence true for projection is to
put the write where the read already is. The alternative — a per-domain projector callback — means
adding a kind is an entry **plus** a callback **plus** a registration, which is not "a single entry",
and Round 1's adversary correctly scored that as failing the criterion.

**Cost, stated:** `entities.py` grows a second interpolated-identifier statement beside `_read`'s
(`entities.py:86`, which already carries `# noqa: S608`), guarded the same way — against the frozen
`ENTITY_KINDS` tuple, never against caller input. That is **one new interpolation site, inside the
module that already owns the only other one.** CLAUDE.md gets an explicit amendment naming
`entities.py` as the sanctioned cross-table status **write**; an undocumented exception is exactly
the debt CLAUDE.md's "Known architectural debt" section already tracks.

---

## 4. Schema — full DDL

`claims.py`, module-level `CLAIMS_SCHEMA`, registered with
`db.register_schema("claims", ensure_schema)`.

```sql
CREATE TABLE IF NOT EXISTS entity_claims (
    claim_id       TEXT PRIMARY KEY,           -- 'CLM-<n>', generated like findings._next_id
    entity_id      TEXT NOT NULL,              -- 'CB-1234' | 'FR-7' | future kinds
    kind           TEXT NOT NULL,              -- EntityKind.name — a VALUE, never an identifier

    holder         TEXT NOT NULL,              -- 'fix-cb-2534-debug-rescue-scope'
    holder_kind    TEXT NOT NULL DEFAULT 'agent'
                     CHECK(holder_kind IN ('branch','agent','human')),
    holder_repo    TEXT,                       -- abs path of the repo owning the branch, else NULL

    claimed_at     TEXT NOT NULL,              -- ISO, types.utc_now()
    renewed_at     TEXT NOT NULL,              -- heartbeat: bumped free on every already_mine
    touch_count    INTEGER NOT NULL DEFAULT 1, -- monotone; THE outcome discriminator (§8.1)
    note           TEXT NOT NULL DEFAULT '',

    prev_status    TEXT,                       -- pre-claim status, NULL if not projected
    projected_to   TEXT,                       -- status we wrote, NULL if not projected

    released_at    TEXT,                       -- NULL == LIVE. Soft delete.
    released_by    TEXT,                       -- who/what closed it
    release_reason TEXT                        -- 'explicit' | 'terminal:<status>' | 'branch merged'
);

-- THE mutual-exclusion primitive: at most one LIVE claim per entity.
-- Independently executed against real SQLite by both Round-2 adversaries; re-executed
-- as probe P1 this run with the corrected identity comparison.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_live
    ON entity_claims(entity_id) WHERE released_at IS NULL;

-- criterion 3, reverse direction: "what does agent-7 hold" — indexed point query, no fold.
CREATE INDEX IF NOT EXISTS idx_claims_holder_live
    ON entity_claims(holder) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_claims_entity ON entity_claims(entity_id);
```

`holder_kind='process'` is **cut** from the CHECK list (it was on my own Round-2 cut list and Opus's
separables list). Adding a value to a CHECK constraint later requires the same table rebuild as
`reqs.py`'s `_migrate_to_lowercase` (`reqs.py:53-97`) — that is the cost, and it is paid only if
someone actually needs a fourth holder kind.

### 4.1 Why `claim_id` PK + partial unique index, and not `entity_id` PK

`entity_id` as PK structurally forbids more than one row per entity, and SQLite cannot drop or
replace a primary key with `ALTER TABLE`. `claim_id TEXT PRIMARY KEY` plus
`CREATE UNIQUE INDEX … WHERE released_at IS NULL` gives **the identical exclusion guarantee** while
allowing unbounded *closed* rows per entity. Verified by execution (P1): after a soft release, a
different holder inserts a **new row** with a new `claim_id` while the old row survives with its
`released_at` set, and the live count stays at exactly 1 throughout.

History is present from day one, not bolted on. That is what makes the terminal hook auditable:
`release_reason='terminal:fixed'` is a queryable record that a commit trailer closed the claim, and
`SELECT release_reason, count(*) FROM entity_claims WHERE released_at IS NOT NULL GROUP BY 1` is the
only evidence that will ever tell us whether the auto-release path works in the field.

**Honest limit on that metric, conceded to Codex:** grouping `release_reason` shows releases that
*happened*, not terminal transitions that *should* have released and did not. The meaningful health
check is the `divergent` flag (§7.4) — a live claim on an entity whose status is already terminal.

### 4.2 No `REFERENCES`, on purpose

`PRAGMA foreign_keys` is OFF, so `REFERENCES findings(id)` would read as enforced and would not be.
`milestone_items.milestone_id … REFERENCES milestones(id)` (`milestones/_schema.py`) is already
decorative; I decline to add a second. Integrity comes from three real mechanisms:

1. **Write-time validation.** `claim()` calls `EntityRef.of(entity_id)` (`ValueError` on bad format,
   `entities.py:78`) then `.require(conn)` (`KeyError` if absent, `entities.py:105-108`) before any
   insert. A claim on a non-existent entity cannot be created through the API.
2. **Read-time orphan reporting.** Every read path evaluates `EntityRef.of(id).exists(conn)` and
   emits `"orphaned": true`. Orphans are reported, never silently dropped.
3. **Precedent.** `milestones/triage.py` already catches `KeyError` for a deleted finding rather than
   relying on cascade. House style.

---

## 5. Transaction discipline

### 5.1 `db.txn` — reentrant, never a plain `BEGIN`, never masks the original exception

```python
# db.py — infrastructure. No domain import.

@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[bool]:
    """BEGIN IMMEDIATE with isolation_level save/restore, reentrant.

    Yields True if THIS frame opened the transaction and will commit it;
    False if a transaction was already open, in which case this frame does
    nothing at all — no BEGIN, no COMMIT, no ROLLBACK — and the owning frame
    keeps full control of the outcome.

    Never write a plain `BEGIN` in this codebase: it pins a read snapshot and
    the later write upgrade dies with SQLITE_BUSY_SNAPSHOT, which busy_timeout
    cannot rescue.
    """
    if conn.in_transaction:
        yield False                      # ambient: the caller owns it
        return

    saved = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield True
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:      # SQLite may have auto-rolled back already
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass                 # cleanup must never replace the real exception
            raise
    finally:
        conn.isolation_level = saved
```

Three deliberate details, each answering an executed failure:

- **`if conn.in_transaction: yield False`.** `in_transaction` is `True` after an explicit
  `BEGIN IMMEDIATE`, so nesting is detected and never attempted. A nested `BEGIN IMMEDIATE` raises
  `SQLITE_ERROR` (code 1), which the contention classifier deliberately re-raises (§9) — so an
  unguarded nesting bug would surface as an unhandled exception, not as a silent `undetermined`.
- **`if conn.in_transaction` guarding the `ROLLBACK`, plus a swallowed `OperationalError`.** A
  `ROLLBACK` with no active transaction raises `SQLITE_ERROR` whose message contains neither "locked"
  nor "busy". Without this guard, the cleanup exception replaces the original and escapes past the
  classifier.
- **`except BaseException`.** A `KeyboardInterrupt` mid-transaction must still roll back.

**Invariant, to be stated in CLAUDE.md and ratcheted by a test:** the codebase contains no plain
`BEGIN`. `db.txn` is the only place `BEGIN IMMEDIATE` is written. Verified this run:
`grep -rn 'BEGIN' src/codebugs/` returns exactly two executable occurrences — `merge.py:242` and
`milestones/capacity.py:182` — both of which are pre-existing and **not** refactored by this design
(deferred, §19). The ratchet test therefore starts as an allowlist of those two known sites plus
`db.txn`, and shrinks when the refactor lands.

### 5.2 Core / wrapper split

| Layer | Names | Transaction | May commit? | Callers |
|---|---|---|---|---|
| **core** | `_claim_core`, `_release_core` | none — emits statements only | **NEVER** | `claims.py` itself, incl. the terminal hook running under an ambient transaction |
| **public** | `claim`, `release` | `with db.txn(conn)` | via `txn` | MCP tools, CLI, external callers |

```python
def claim(conn, *, entity_id, holder, ...) -> dict[str, Any]:
    try:
        with db.txn(conn):
            return _claim_core(conn, entity_id=entity_id, holder=holder, ...)
    except sqlite3.OperationalError as exc:
        return _undetermined(exc)        # §9 — classified on sqlite_errorcode
```

The split is chosen over a `manage_txn: bool` flag because a boolean puts the hazard in the call
site's hands, whereas two names put it in the reader's face.

**Where the ambient case actually occurs, traced:** `findings.update_finding` uses Python sqlite3's
implicit transaction management. Its `UPDATE findings …` at `findings.py:298` opens a write
transaction and takes the RESERVED lock; `conn.commit()` follows at `:299`. The terminal hook fires
between those two lines, so at hook time `conn.in_transaction` is `True` **and the write lock is
already held** — there is no deferred-to-immediate upgrade to lose. `_release_core` emits its
statements into that transaction and returns; `findings.py:299` commits both the status change and
the release atomically, or neither lands.

**Enforcement, not documentation:** a test opens a transaction, calls every `_*_core` function, and
asserts `conn.in_transaction` is still `True` afterwards. A core function that commits fails it.

### 5.3 `busy_timeout`, explicitly

`db.connect()` (`db.py:492-503`) sets `journal_mode=WAL` at `:497` and nothing else. The 5000 ms that
turns a losing writer into a clean result rather than an exception is currently **inherited from
`sqlite3.connect(timeout=5.0)`'s default and appears nowhere in the source.** One line, its own
behaviour-neutral commit, landing ahead of everything else here:

```python
conn.execute("PRAGMA busy_timeout=5000")   # explicit; was inherited from sqlite3's default
```

`claims.py` does not re-set it. One owner for the setting.

---

## 6. Public API — every signature

Keyword-only after `conn`, per CLAUDE.md. Type hints on every public signature.

```python
# claims.py

CLAIMS_SCHEMA: str
def ensure_schema(conn: sqlite3.Connection) -> None: ...

# --- core layer: emits statements, NEVER commits, NEVER opens a transaction ---
def _claim_core(
    conn: sqlite3.Connection, *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    note: str = "",
    project: bool = True,
    allow_terminal: bool = False,
) -> dict[str, Any]: ...

def _release_core(
    conn: sqlite3.Connection, *,
    entity_id: str,
    holder: str,
    restore_status: bool = True,
    reason: str = "explicit",
    released_by: str | None = None,
) -> dict[str, Any]: ...

# --- public layer: transaction-managing, contention-classifying ---
def claim(
    conn: sqlite3.Connection, *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    note: str = "",
    project: bool = True,
    allow_terminal: bool = False,
) -> dict[str, Any]: ...

def release(
    conn: sqlite3.Connection, *,
    entity_id: str,
    holder: str,
    restore_status: bool = True,
    reason: str = "explicit",
) -> dict[str, Any]: ...

# --- read layer: no transaction, no writes ---
def who_holds(conn: sqlite3.Connection, *, entity_id: str) -> dict[str, Any] | None: ...

def held_by(conn: sqlite3.Connection, *, holder: str) -> dict[str, Any]: ...

def list_claims(
    conn: sqlite3.Connection, *,
    kind: str | None = None,
    holder: str | None = None,
    holder_kind: str | None = None,
    divergent_only: bool = False,
    stale_after_seconds: int | None = None,
    include_released: bool = False,
    limit: int = 200,
) -> dict[str, Any]: ...

# --- module-level registration, per CLAUDE.md ---
def register_tools(mcp, conn_factory) -> None: ...
def register_cli(sub, commands) -> None: ...

db.register_schema("claims", ensure_schema)
db.register_tool_provider("claims", register_tools)
db.register_cli_provider("claims", register_cli)
db.register_status_change_hook("claims_auto_release", _auto_release_on_terminal)
```

```python
# entities.py — the two additions

@dataclass(frozen=True)
class EntityKind:
    ...                                     # entities.py:23-33, unchanged
    busy_status: str | None = None          # NEW, trailing, defaulted — see §10

class EntityRef:
    def set_status(self, conn: sqlite3.Connection, *, new_status: str, expected: str) -> bool: ...
```

```python
# db.py — the three additions

@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[bool]: ...

def register_status_change_hook(
    name: str,
    fn: Callable[[sqlite3.Connection, str, str | None, str], None],
) -> None: ...

def run_status_change_hooks(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None: ...
```

**No `steal`. No `history`. No `audit`. No `git_branch_verifier`. No `prune`.** All deferred (§19),
and none of them is referenced by any code path in this document.

---

## 7. Exact SQL, every path

### 7.1 Claim — the guard

Runs inside the same `BEGIN IMMEDIATE`, before the upsert. **This is the M1 fix: the terminal check is
unconditional, gated only on `allow_terminal`.**

```python
ref = entities.EntityRef.of(entity_id)      # ValueError on bad format          (entities.py:78)
ref.require(conn)                           # KeyError if absent                (entities.py:105)

current = ref.status(conn)                  # ALWAYS read — never gated on projection
if current in ref.kind.terminal and not allow_terminal:
    return {"outcome": "entity_terminal", "entity_id": entity_id, "kind": ref.kind.name,
            "current_status": current, "holder": None}

busy = ref.kind.busy_status                 # declarative; None == this kind does not project
do_project = project and busy is not None
```

Round 2 wrote `if project and busy is not None:` around the whole block, which made the terminal
guard unreachable for **every requirement** (`busy_status is None`) and for every `--no-project`
claim. Both adversaries scored it; it is fixed by hoisting the terminal test out of both conditions.
`ref.kind.terminal` is populated for both existing kinds — `FINDING_TERMINAL` (`types.py:36`) and
`REQUIREMENT_TERMINAL` (`types.py:41`) — so the guard is live for requirements from day one even
though requirements never project.

### 7.2 Claim — the upsert

One statement. **The `WHERE` compares the full holder triple with NULL-safe `IS` on the nullable
column — this is the M3 fix, executed as probe P1.**

```sql
INSERT INTO entity_claims
    (claim_id, entity_id, kind, holder, holder_kind, holder_repo,
     claimed_at, renewed_at, touch_count, note, prev_status, projected_to,
     released_at, released_by, release_reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, NULL)
ON CONFLICT(entity_id) WHERE released_at IS NULL DO UPDATE SET
       renewed_at  = excluded.renewed_at,
       touch_count = entity_claims.touch_count + 1,
       note        = CASE WHEN excluded.note <> '' THEN excluded.note
                          ELSE entity_claims.note END
 WHERE entity_claims.holder      =  excluded.holder
   AND entity_claims.holder_kind =  excluded.holder_kind
   AND entity_claims.holder_repo IS excluded.holder_repo
RETURNING claim_id, holder, holder_kind, holder_repo, claimed_at, renewed_at,
          touch_count, prev_status, projected_to,
          (touch_count = 1) AS was_new;
```

Executed results (P1, SQLite 3.47.1):

| Case | `RETURNING` row | Outcome |
|---|---|---|
| no live claim | `touch_count=1, was_new=1` | `claimed` |
| live claim, identical `(holder, holder_kind, holder_repo)` | `touch_count=2, was_new=0` | `already_mine`, `renewed_at` moved |
| live claim, same holder, **different `holder_repo`** | **`None`** | `held_by_other` |
| live claim, same holder+repo, **different `holder_kind`** | **`None`** | `held_by_other` |
| live claim with `holder_repo IS NULL`, repeated with NULL | `touch_count=2` | `already_mine` — `IS` matches NULL to NULL |
| `sqlite_errorcode & 0xFF ∈ {5, 6}` | — | `undetermined` |

`was_new` is computed **in the row**, not from `cursor.rowcount` — the same idiom as `sweep.py:313`.

**Consequence of full-triple identity, stated:** a same-holder retry that supplies *corrected*
metadata (a `holder_repo` that was wrong or missing the first time) is refused as `held_by_other`
rather than repairing the row. That is deliberate: silently overwriting ownership evidence on a live
claim is worse than a refusal that names the incumbent. The repair path is
`release` then `claim`, and the `held_by_other` response carries the incumbent's full triple so the
caller can see *why* it looked like a mismatch. `holder_repo` is informational in v1 — nothing reads
it, because the git verifier is deferred — so this costs nothing today and is the right default when
the verifier lands.

### 7.3 Claim — naming the incumbent, and projecting

**On `held_by_other`** (the upsert returned `None`), one parameterized read in the same transaction:

```sql
SELECT claim_id, holder, holder_kind, holder_repo, claimed_at, renewed_at,
       touch_count, note, prev_status, projected_to
  FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;
```

**On `claimed`, and only if `do_project`:**

```python
moved = ref.set_status(conn, new_status=busy, expected=current)     # §10.2, rowcount-based
conn.execute(
    "UPDATE entity_claims SET prev_status = ?, projected_to = ? WHERE claim_id = ?",
    (current, busy if moved else None, claim_id),
)
```

If `moved` is False the status changed between the guard read and the projection — impossible from
another process (we hold the write lock from `BEGIN IMMEDIATE`) and, since this design registers no
hook that fires between those two statements, it has no known execution path on the same connection
either. It is checked anyway because the cost is one boolean and the failure mode without the check
is a `prev_status` that release would restore to a value that was never current. `projected_to` stays
NULL, release will not attempt a restore, and the response carries
`projected: false, projection: "raced"`.

**`claim_id` generation** follows `findings._next_id` (`findings.py:117`) in shape:
`SELECT claim_id FROM entity_claims ORDER BY CAST(SUBSTR(claim_id, 5) AS INTEGER) DESC LIMIT 1` over
`claim_id LIKE 'CLM-%'`, executed **inside** the same `BEGIN IMMEDIATE`, so the read-then-insert is
not a race.

### 7.4 Release

```sql
UPDATE entity_claims
   SET released_at = ?, released_by = ?, release_reason = ?
 WHERE entity_id = ? AND holder = ? AND released_at IS NULL
RETURNING claim_id, prev_status, projected_to;
```

`cur.fetchone()` — **never `rowcount`** (§7.6).

| Result | Outcome |
|---|---|
| row | `released` |
| `None`, and a live claim exists for a different holder | `not_yours` (response names the real holder) |
| `None`, and no live claim exists | `not_claimed` |

Note the release `WHERE` matches on `holder` **only**, not the full triple. That is deliberate and
asymmetric with §7.2: a claim taken by `(branch, /repo/x)` must be releasable by
`worktree-finish.sh`, which knows `${BRANCH}` (`:647`) but has no reason to reconstruct the same
`--repo` string. Tightening identity is the right default for *taking* ownership and the wrong
default for *giving it back* — a release that refuses on a metadata mismatch leaks the claim, which
is the exact pathology this design exists to remove.

Then, **only if `projected_to IS NOT NULL` and `restore_status`**:

```python
restored = ref.set_status(conn, new_status=prev_status, expected=projected_to)
```

which is `UPDATE <table> SET status=?, updated_at=? WHERE id=? AND status=?`. If the holder already
moved the finding to `fixed`, the `status = projected_to` guard fails, `rowcount == 0`, and the
status is left alone. **Release never resurrects finished work.** The response reports
`status_restored: false, current_status: "fixed"`.

### 7.5 Read paths — criterion 3, two indexed point queries

```sql
-- who_holds(CB-1234): uses idx_claims_live
SELECT * FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;

-- held_by('fix-cb-2534-…'): uses idx_claims_holder_live. Point query, no fold.
SELECT * FROM entity_claims WHERE holder = ? AND released_at IS NULL ORDER BY claimed_at;

-- list_claims: composable, all filters optional
SELECT * FROM entity_claims
 WHERE (:kind        IS NULL OR kind        = :kind)
   AND (:holder      IS NULL OR holder      = :holder)
   AND (:holder_kind IS NULL OR holder_kind = :holder_kind)
   AND (:include_released OR released_at IS NULL)
 ORDER BY renewed_at DESC LIMIT :limit;
```

Every returned row is decorated in Python with:

| Field | Meaning |
|---|---|
| `held_seconds` | `now - claimed_at` |
| `idle_seconds` | `now - renewed_at` — the honest staleness signal |
| `stale` | `idle_seconds > stale_after_seconds`, **only when the caller supplied a threshold.** Never a baked-in default: the reader chooses, the tracker does not guess |
| `orphaned` | `not EntityRef.of(entity_id).exists(conn)` (§4.2) |
| `divergent` | live claim **and** `EntityRef.of(entity_id).is_resolved(conn)` (`entities.py:100-103`) — a claim on an already-terminal entity |

`divergent` is the read-time safety net for a terminal hook that failed, and `divergent_only=True`
makes *"show me every claim the hook missed"* a single call. **This is the health metric, not the
`release_reason` histogram** — the histogram counts releases that happened, `divergent` counts the
ones that should have happened and did not.

`stale_after_seconds` and `divergent_only` are filtered in Python after the SQL, not pushed into the
`WHERE`, because both depend on values (`now`, the entity's current status) that are not columns of
this table. With `limit` defaulting to 200 that is at most 200 `EntityRef.status` point reads per
call, each an indexed primary-key lookup.

### 7.6 The `RETURNING` audit — every statement in this design

| # | Statement | `RETURNING`? | Outcome read from | Safe? |
|---|---|---|---|---|
| 1 | claim upsert (§7.2) | yes | `cur.fetchone()`, incl. computed `was_new` | yes |
| 2 | held_by_other lookup (§7.3) | no | `fetchone()` on a `SELECT` | yes |
| 3 | `UPDATE entity_claims SET prev_status…` (§7.3) | no | not consulted | yes |
| 4 | `EntityRef.set_status` (§10.2) | **no, deliberately** | `cur.rowcount` — valid *because* there is no `RETURNING` | yes |
| 5 | release soft-delete (§7.4) | yes | `cur.fetchone()` | yes |
| 6 | all read paths (§7.5) | n/a | `fetchall()` | yes |

**Rule for the implementation, to be stated in CLAUDE.md:** *a statement either carries `RETURNING`
and its outcome is read by fetching, or it carries no `RETURNING` and its outcome is read from
`rowcount`. Never both.* Executed this run: for `UPDATE t SET a=2 WHERE a=1 RETURNING a`,
`cur.rowcount` is **0 before** the fetch and **1 after**; the same statement without `RETURNING`
gives `1` on a hit and `0` on a miss immediately. So a `RETURNING` statement whose outcome is read
from `rowcount` reports "nothing happened" **while having performed the write** — strictly worse than
a no-op. The in-repo precedent for the correct idiom already exists at `sweep.py:313`, verbatim:
`RETURNING (recurrence_count = 1) AS was_new` followed by `.fetchone()` at `sweep.py:315`.

---

## 8. Outcome vocabulary — complete

```
claim   → claimed | already_mine | held_by_other | entity_terminal | undetermined
release → released | not_yours   | not_claimed   | undetermined
```

There are no other outcomes. `who_holds`, `held_by` and `list_claims` are reads and return rows, not
outcomes.

Every response dict from `claim` / `release` carries these keys, always present, `None` where not
applicable:

| Key | Notes |
|---|---|
| `outcome` | one of the eight strings above |
| `entity_id`, `kind` | echoed from the request / resolved from `EntityRef` |
| `holder`, `holder_kind`, `holder_repo` | **the live claim's** holder triple — on `held_by_other` this is the *incumbent*, not the caller |
| `claim_id`, `claimed_at`, `renewed_at`, `touch_count` | of the live row |
| `held_seconds`, `idle_seconds` | derived (§7.5) |
| `projected`, `projected_to`, `prev_status` | projection state |
| `orphaned`, `divergent` | derived (§7.5) |

Outcome-specific additions:

| Outcome | Extra keys |
|---|---|
| `entity_terminal` | `current_status` |
| `undetermined` | `reason` (`"database_busy"`), `retry_after_ms` (`250`), `detail` (the exception string) |
| `released` | `status_restored: bool`, `current_status` |
| `claimed` with a raced projection | `projection: "raced"` |

### 8.1 The discriminator is `touch_count`, never a timestamp

`types.utc_now()` (`types.py:12-14`) formats with `"%Y-%m-%dT%H:%M:%SZ"` — **whole seconds**. Two
calls inside the same second produce equal strings, so a `claimed_at == renewed_at` discriminator
misreports a retry as a fresh claim. An agent retrying on a 250 ms loop does exactly this.
`touch_count` is a monotone integer incremented by the upsert itself and is clock-independent;
`was_new` is computed from it inside the `RETURNING` clause (P1: `was_new` came back `1` then `0` on
two calls in the same wall-clock second). `utc_now` itself is **not** modified — changing a timestamp
format used by nine modules to fix one discriminator is the wrong trade.

### 8.2 `entity_terminal` — why it is an outcome and not an error

Claiming a `fixed` finding is not a coordination event, it is a mistake. The live script already
knows this and works around it in shell: `worktree-setup.sh:205-207` reads *"Only `open` cards are
flipped (decided during the guard above) — a follow-up branch on a `fixed` card must not silently
reopen it."* That rule lives today in a bash comment plus the `case "${status}"` at `:114-134`, fed
by a `codebugs get | sed` pre-read at `:112-113`.

Encoding it in `claim()` lets the shell **delete** that pre-read — which is a textbook check-then-act
race, in shell, unguarded. **So the design removes a race from the live call site rather than only
adding a mechanism beside it.** `allow_terminal=True` exists for the deliberate case (reopening a
regression); it records the claim and does not project.

### 8.3 `undetermined` — what a caller must do with it

**`undetermined` means: the database was too contended to tell you whether the claim was made.**
The claim may or may not exist. **Re-issue the identical call.** That is safe because the primitive
is an idempotent upsert: the same `(entity_id, holder, holder_kind, holder_repo)` quadruple replayed
converges on `already_mine` and can never double-claim. That idempotence is the entire reason the
claim is an upsert rather than a bare `INSERT`.

`retry_after_ms: 250` is a suggestion, not a contract. The shell caller (§15.2) retries exactly once
and then proceeds **unclaimed with a loud warning** rather than blocking a human's worktree setup on
database contention.

---

## 9. Error handling

Three tiers, matching CLAUDE.md's contract exactly.

| Condition | Behaviour |
|---|---|
| Unparseable entity id | `ValueError` from `EntityRef.of` (`entities.py:78`) — **propagates** |
| Well-formed id, no such row | `KeyError` from `EntityRef.require` (`entities.py:105-108`) — **propagates** |
| SQLite contention (`SQLITE_BUSY` / `SQLITE_LOCKED`) | caught, returned as `outcome="undetermined"` |
| Anything else | **propagates** |

MCP tools let all of it reach FastMCP's built-in error handling. CLI handlers catch
`ValueError`/`KeyError`, print to stderr, `sys.exit(1)` — the `_cmd_update` / `_cmd_get` pattern in
`findings.py`.

The contention classifier keys on **numeric codes, not message strings**:

```python
_CONTENTION = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})    # 5, 6

def _is_contention(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in _CONTENTION          # masks extended codes: 517 & 0xFF == 5

def _undetermined(exc: sqlite3.OperationalError) -> dict[str, Any]:
    if not _is_contention(exc):
        raise exc                                # a real error is NEVER masked as contention
    return {"outcome": "undetermined", "reason": "database_busy",
            "retry_after_ms": 250, "detail": str(exc)}
```

`SQLITE_BUSY_SNAPSHOT` (517) masks to 5 and is classified as contention. `cannot start a transaction
within a transaction` and `cannot rollback - no transaction is active` are both `SQLITE_ERROR`
(code 1) and are **re-raised** — those are programming errors and must be loud. `sqlite_errorcode`
requires Python 3.11+, which the project already requires.

The `raise exc` inside `_undetermined` re-raises the original exception object with its traceback
intact, from inside the `except` block that caught it — no wrapping, no chaining noise.

---

## 10. The projection contract, and its preconditions

### 10.1 Declaration, not registration

Projection is one optional field on the existing frozen descriptor at `entities.py:23-33`:

```python
@dataclass(frozen=True)
class EntityKind:
    name: str
    table: str
    id_pattern: re.Pattern[str]
    terminal: frozenset[str]
    sort_col: str
    result_key: str
    readable_cols: frozenset[str]
    busy_status: str | None = None          # NEW — declared, not registered
```

```python
ENTITY_KINDS = (
    EntityKind(name=t.ENTITY_FINDING,     table="findings",     …, busy_status="in_progress"),
    EntityKind(name=t.ENTITY_REQUIREMENT, table="requirements", …),   # None: does not project
)
```

`claims.py` branches on nothing — it reads `ref.kind.busy_status`. If a kind declares one, claiming
projects; if it does not, claiming records ownership and touches no status.

**This is what the user ratified.** Requirements get claim records; claiming a requirement does
**not** change its status; `reqs.py:22-23`'s CHECK constraint is **not** rebuilt. `"in_progress"` is
not in `REQUIREMENT_STATUSES` (`types.py:39`), so a projecting requirement would have required that
rebuild — and it is not happening.

### 10.2 `EntityRef.set_status` — the one sanctioned status write

```python
def set_status(self, conn: sqlite3.Connection, *, new_status: str, expected: str) -> bool:
    """Guarded status write. THE single sanctioned cross-table status write.

    Runs inside the caller's transaction and MUST NOT commit — the caller
    composes it with other writes.  Returns True iff the row moved.

    Deliberately does NOT use RETURNING: rowcount is the correct outcome idiom
    precisely when RETURNING is absent (CLAUDE.md 'RETURNING rule').
    Deliberately does NOT fire status-change hooks (see the invariant below).
    """
    cur = conn.execute(
        f"UPDATE {self.kind.table} SET status = ?, updated_at = ? WHERE id = ? AND status = ?",  # noqa: S608
        (new_status, t.utc_now(), self.id, expected),
    )
    return cur.rowcount == 1
```

Interpolation safety: `self.kind.table` comes from the frozen `ENTITY_KINDS` tuple
(`entities.py:36-55`) and can never be caller input — the same closed-world argument that already
licenses `entities.py:86`'s `# noqa: S608`.

**Invariant — no hook, therefore no recursion.** `set_status` is called only by `claims.py`, only
with `kind.busy_status` (never terminal by construction) or with a `prev_status` that was
non-terminal at claim time (guaranteed by §7.1's unconditional terminal guard). Therefore no
`set_status` call can ever produce a terminal status, therefore the terminal hook has nothing to
react to, and `claim → project → hook → release → restore → hook → …` is unreachable. Test 19 asserts
it: a claim followed by a release fires **zero** status-change hooks.

**Why not route through `update_finding`?** Because `findings.py:299` calls `conn.commit()`. Routing
projection through it would commit the status change independently of the claim, so a later failure
would leave a projected status with no claim. `set_status` is the reason the claim and the projection
land in one transaction or neither.

### 10.3 Preconditions on a projecting `EntityKind` — the M7 fix

Round 2 said "a third kind is one `EntityKind` entry". That is syntactically true and semantically
incomplete: it silently assumed four things about the target table. They are now a **written
contract**, and an executed test enforces them.

> **A kind that declares `busy_status` MUST satisfy all four:**
>
> **P1. Schema shape.** Its table has columns `id` (TEXT PK), `status` (TEXT), `updated_at` (TEXT).
> `set_status` writes exactly `status` and `updated_at` and keys on `id`.
>
> **P2. Value admissibility.** The declared `busy_status` value is accepted by the table — it passes
> any CHECK constraint on `status` **and** it is a canonical value, not an alias. `set_status`
> performs **no** resolution: it writes the declared string verbatim, so a kind declaring
> `busy_status="active"` would write `"active"` even though `FINDING_STATUS_ALIASES` maps it to
> `"in_progress"` (`types.py:33`). Declare canonical values only.
>
> **P3. `busy_status ∉ kind.terminal`.** Otherwise projection would create a terminal status, which
> breaks §10.2's no-recursion invariant and would make a claim self-releasing.
>
> **P4. Side-effect tolerance.** The kind's domain module accepts that a projected status change does
> **not** run its `update_*` function — no note appended, no `meta` touched, no status-change hook
> fired. The audit trail for a projection lives in `entity_claims`
> (`prev_status`, `projected_to`, `claimed_at`, `release_reason`) instead.

**Enforced, not just documented** — test 5b iterates `ENTITY_KINDS`, and for every kind with
`busy_status is not None`:

- reads `PRAGMA table_info(<kind.table>)` and asserts `{"id","status","updated_at"} ⊆ columns` (P1);
- inserts a throwaway row, calls `set_status` with the declared `busy_status`, asserts it returns
  `True` and the stored value is byte-identical to the declaration, then rolls back (P2);
- asserts `kind.busy_status not in kind.terminal` (P3).

P4 is a review obligation, not a testable one, and it is stated as such.

### 10.4 Bypass cost, stated

`set_status` writes `status` and `updated_at` and nothing else. A projection therefore does **not**
appear in the finding's notes history: an operator reading `codebugs get CB-1234` sees the status
change with no explanation. Round 2 proposed adding a `claim` block to `codebugs get`'s response to
close this; that is on the deferred list (§19), so **in v1 the explanation lives one command away**,
in `codebugs who-holds CB-1234`. That is a real ergonomic cost and I am not calling it free.

---

## 11. The status-change hook seam — and the CAS `changed` guard (M2)

### 11.1 The seam

```python
# db.py — infrastructure, no domain import. Symmetric with register_post_add_hook (db.py:178-190).

def register_status_change_hook(
    name: str,
    fn: Callable[[sqlite3.Connection, str, str | None, str], None],
) -> None:
    """Register a hook that runs when an entity's status is CHANGED through a
    domain update function. Hooks run inside the caller's transaction, before the
    final commit, so the status change and any hook side-effects land atomically.
    Name-keyed: module re-import is a no-op (matches register_post_add_hook)."""

def run_status_change_hooks(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Invoke every registered hook. Failures go to stderr, never raised — a status
    write must always succeed.

    CONTRACT FOR CALLERS: call this ONLY when the status write actually changed the
    row. See db.register_status_change_hook's docstring and claims §11.2."""
```

This mirrors `register_post_add_hook` (`db.py:178-190`) / `run_post_add_hooks` (`db.py:193`) exactly:
same registration discipline, same in-transaction contract, same swallow-and-log failure policy. The
create side of that pair already exists; the update side does not. **The asymmetry is the anomaly.**

### 11.2 The firing rule — this is the M2 fix

Round 2 fired the hook when "the intended status differs from the pre-read row's status". That is not
the same as "the write happened", and Codex was right to score it PARTIAL: a refused write could
still fire the hook and release ownership on an entity whose status never became terminal.

**The rule is now: fire iff the write changed the row.** In `findings.update_finding`:

```python
    # findings.py — replaces the bare `conn.execute(...)` at :298
    old_status = row["status"]                       # from the SELECT * at findings.py:252
    cur = conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
    changed = (
        status is not None                           # a status was requested
        and cur.rowcount == 1                        # the row was actually written
        and status != old_status                     # ...to a different value
    )
    if changed:
        db.run_status_change_hooks(conn, finding_id, old_status, status)
    conn.commit()                                    # findings.py:299 — unchanged
```

`status` at that point is already canonicalised by `resolve_finding_status` (`findings.py:260`), so
the comparison is canonical-to-canonical and an alias (`"done"` → `"fixed"`) does not read as a
change when the row is already `fixed`.

The identical shape goes into `reqs.update_requirement` between `reqs.py:222` and `:223`, with
`resolve_requirement_status` (`reqs.py:188`) providing the canonical value. **Both writers fire it —
that is what makes this a seam over the entity layer rather than a findings-specific callback wearing
a general name.**

**Honesty about what `changed` buys today.** `expected_status` is DEFERRED (§19), so today's `UPDATE`
has no CAS predicate and `cur.rowcount` is 0 only if the row vanished between `findings.py:252` and
`:298`. The `changed` guard is therefore **defensive in v1 and load-bearing the moment
`expected_status` lands** — at which point the `WHERE` gains `AND status = ?` and `rowcount == 0`
becomes the ordinary refusal signal. Writing the guard now means the refusal path is already correct
when the CAS arrives, instead of being a defect introduced by a later commit. I am not claiming it
prevents an observable bug today.

### 11.3 The hook `claims.py` registers

```python
def _auto_release_on_terminal(conn, entity_id, old_status, new_status) -> None:
    ref = entities.EntityRef.of(entity_id)              # ValueError → swallowed by the runner
    if new_status not in ref.kind.terminal:
        return
    live = _live_claim(conn, entity_id)
    if live is None:
        return
    _release_core(conn, entity_id=entity_id, holder=live["holder"],
                  restore_status=False,                 # the entity is FINISHED; never restore
                  reason=f"terminal:{new_status}", released_by="hook:status_change")

db.register_status_change_hook("claims_auto_release", _auto_release_on_terminal)
```

`restore_status=False` is load-bearing: a terminal status is the *point*, and restoring `prev_status`
would undo the finding's resolution.

`_release_core`, not `release` — the hook runs inside `update_finding`'s open transaction (§5.2), so
it must emit statements and not manage a transaction. This is the sole reason the core layer exists
now that `pull_next` integration is deferred.

### 11.4 The end-to-end sequence this exists for

An agent claims CB-1234 → `entity_claims` row live, `findings.status='in_progress'`,
`prev_status='open'`, `projected_to='in_progress'`. The agent commits `Fixes: CB-1234`. On
integration, `worktree-finish.sh:1249-1257` runs `auto-resolve-codebugs.py`, which calls
`codebugs update CB-1234 --status fixed` → `update_finding` → `changed=True` →
`run_status_change_hooks(conn, 'CB-1234', 'in_progress', 'fixed')` → the claim is soft-closed with
`release_reason='terminal:fixed'`, **inside the same transaction as the status write**, committed
together at `findings.py:299`.

Final state: CB-1234 `fixed`, unclaimed, and the record says a commit trailer closed it.

**What this does NOT do:** it does not make the provenance path claim-aware, and it does not stop a
commit from resolving a finding held by someone else. That is correct — a commit that fixes a bug
fixes it regardless of who held the card.

### 11.5 Disclosed cost, and the fallback if reviewers reject the seam

Hook exceptions are swallowed (copying `run_post_add_hooks`' policy deliberately: a failed
auto-release must never break a status write). The degraded state is a live claim on a terminal
entity — which is **exactly** what `divergent` reports (§7.5) and what `claims --divergent`
enumerates. **If the seam is rejected in review, the design degrades to read-time divergence
reporting: visible and manually recoverable, rather than silently wrong.** It costs the automatic
clearer, not the design.

I will not overclaim here: swallowing exceptions means "the clearers keep the table trustworthy" is
an aspiration backed by a *visible* failure mode, not a guarantee. Codex was right to score the
stronger claim as unsupported.

---

## 12. CLI surface

`claims.py` provides `register_cli(sub, commands)` and calls
`db.register_cli_provider("claims", register_cli)` at module level, per CLAUDE.md.

### 12.1 Four verbs. Not seven.

```
codebugs claim <ID>     --holder H [--holder-kind branch|agent|human] [--repo PATH]
                        [--note TEXT] [--no-project] [--allow-terminal] [--json]
codebugs release <ID>   --holder H [--no-restore] [--reason TEXT] [--json]
codebugs who-holds <ID> [--json]
codebugs claims         [--holder H] [--kind K] [--holder-kind K] [--divergent]
                        [--stale-after SEC] [--all] [--format ids|table] [--json]
```

`--format ids` on `codebugs claims` prints **one bare entity id per line and nothing else** — no
header, no decoration, empty output when there are no matches. This is the ID output mode the Judge
ruled IN, and it is what lets `worktree-finish.sh` (§15.3) drive a loop without grep-parsing JSON.
Exit status is `0` on an empty result set: "you hold nothing" is a successful answer, not an error.

`--format table` (the default for `claims`) uses `fmt.format_table`. `--json` emits the full response
dict on every verb. Human output for `claim` / `release` / `who-holds` is one stable line on stdout.

### 12.2 Exit codes are the API for shell callers

This is what makes the surface usable from a `set -euo pipefail` script without parsing.

| code | `claim` | `release` | `who-holds` | `claims` |
|---|---|---|---|---|
| 0 | `claimed` or `already_mine` → proceed | `released` or `not_claimed` → done either way | held (prints the holder line) | success, including an empty list |
| 1 | error: bad id format, no such entity, DB not found | same | same | same |
| 3 | `held_by_other` → refuse | `not_yours` | **not held** | — |
| 4 | `entity_terminal` → refuse | — | — | — |
| 5 | `undetermined` → retry | `undetermined` | — | — |

**`who-holds` returns 0/3 only** — Round 2 gave it a 3/4 split ("released, therefore leftovers" vs
"never claimed") that existed solely to feed §13.2(a)'s false-positive downgrade. That arm is cut
(R6), so the split has no consumer and is removed. Two states: someone holds it, or nobody does.

**`release` returns 0 on `not_claimed`** deliberately. The finish script calls release
unconditionally; "there was nothing to release" is the expected, successful, common case.

### 12.3 What the CLI does not have

No `steal`, no `claim-history`, no `claims-audit`, no `--prune`, no `--before`. Round 2's §3.1 prose
promised `claims prune --before <iso>` and never put it in the CLI surface; **that promise is
withdrawn** (R16), not silently kept.

### 12.4 Retention

**None in v1, and no verb for it.** A closed claim row is ~200 bytes and the realistic rate is tens
per week. If it ever matters, retention is a `DELETE FROM entity_claims WHERE released_at < ?` —
a query, not a migration — and it can be added as a verb at that point with full knowledge of what
the table actually accumulated.

---

## 13. MCP tools

`claims.py` defines `register_tools(mcp, conn_factory)` and calls
`db.register_tool_provider("claims", register_tools)` at module level. All five return
`dict[str, Any]` and let exceptions propagate to FastMCP, per CLAUDE.md.

```python
claims_claim(entity_id: str, holder: str, holder_kind: str = "agent",
             holder_repo: str | None = None, note: str = "",
             project: bool = True, allow_terminal: bool = False) -> dict[str, Any]
claims_release(entity_id: str, holder: str, restore_status: bool = True,
               reason: str = "explicit") -> dict[str, Any]
claims_who_holds(entity_id: str) -> dict[str, Any]
claims_held_by(holder: str) -> dict[str, Any]
claims_list(kind: str | None = None, holder: str | None = None,
            holder_kind: str | None = None, divergent_only: bool = False,
            stale_after_seconds: int | None = None,
            include_released: bool = False, limit: int = 200) -> dict[str, Any]
```

Domain-prefixed per CLAUDE.md's naming rule (the findings and milestones exceptions do not apply —
this is a new module with no external consumers naming its tools).

`claims_who_holds` returns `{"held": false, "entity_id": …}` rather than `None` when nothing holds
it, because an MCP tool must return a dict.

**No git subprocess is spawned from any MCP request.** Round 2's audit tool passed
`git_branch_verifier` and would have launched up to 2N sequential git processes with a 10-second
timeout each; that tool is deferred and this surface has no path to a subprocess at all. M5 is
dissolved rather than fixed.

---

## 14. Migration and back-compat

**Schema.** `entity_claims` is a new table created by `CREATE TABLE IF NOT EXISTS` inside
`claims.ensure_schema`, registered via `db.register_schema("claims", ensure_schema)`. Additive by
construction. **No existing table is altered** — not `findings`, not `requirements`, not
`milestone_items`, and specifically **not `reqs.py:22-23`'s CHECK constraint**.

**`EntityKind.busy_status`** is a trailing field with a `None` default on a frozen dataclass, so
every existing `EntityKind(...)` construction and every unpacking of `ENTITY_KINDS` keeps working
unchanged. `tests/test_entities.py` needs no edit.

**API back-compat.**

- `update_finding` / `update_requirement`: **no signature change.** The only difference is an
  in-function hook call. Response dicts are unchanged. (`expected_status` / `changed` are deferred —
  §19 — so the `"changed"` response key Round 2 promised does **not** appear in v1.)
- No MCP tool signature changes. No CLI flag changes to existing verbs.
- **No behaviour change to `pull_next`.** Round 2 made it project and refuse terminal entities;
  that integration is deferred, so `pull_next` is byte-identical to today.

**Wiring, per CLAUDE.md's "Current rules for new code":**

| File | Change |
|---|---|
| `db.py:487` | add `claims` to the `from codebugs import …` list in `_ensure_modules_loaded()`. This import is what makes `register_status_change_hook` run at all — without it the hook never registers and the terminal clearer silently does not exist. |
| `server.py:22-32` | `SERVER_NAMES["claims"] = "codeclaims"` |
| `cli.py:49` | add `"claims"` to the `--mode` `choices` list |

**No data migration. No backfill of the ~41 existing `in_progress` findings.** The table starts empty
*on purpose*. Backfilling would import exactly the garbage that makes the current signal ungateable,
with invented holders and invented claim times. Those 41 remain a `findings.status` problem, visible
via `codebugs query --status in_progress`, and they are someone's cleanup task — not this table's
contents.

**Consequence, stated plainly:** on day one, `who-holds` returns "not held" for every card in flight,
because nobody has claimed anything yet. The gate is therefore permissive at launch and becomes
protective as claims accumulate — but the *atomic* property (§1.1) is available from the first
concurrent pair, with no warm-up.

**Rollback.** `DROP TABLE entity_claims` plus reverting the code leaves `findings` and `requirements`
exactly as they were, because the only thing claims ever wrote into them is a status value they were
always allowed to hold.

**CLAUDE.md amendments this design requires** (each is otherwise undiscoverable):

1. `entities.py` is the sanctioned cross-table status **write** as well as read; `EntityRef.set_status`
   is the only such write (§3.2).
2. The `RETURNING` rule: a statement reads its outcome by fetching **or** from `rowcount`, never both
   (§7.6).
3. Never write a plain `BEGIN`; `db.txn` is the only place `BEGIN IMMEDIATE` should appear. Known
   pre-existing exceptions: `merge.py:242`, `milestones/capacity.py:182` (§19).
4. A `Claims module` section alongside the existing `Milestones module` section.
5. `register_status_change_hook` documented beside `register_post_add_hook` in the debt/extension-point
   bullet.

---

## 15. Shell adoption — exact diffs, anchors re-derived this run

**Every anchor below was opened this run** against the absolute paths, with true line numbers (§0.1,
§0.2). Round 2's anchors were wrong (R1) and are not reused.

### 15.0 The structural rule these diffs obey

> **Exactly ONE new `codebugs` call may be fatal: the claim gate, placed BEFORE `git worktree add`
> (`worktree-setup.sh:143`). Every other new call is `if`-guarded or `|| true`.**

That matches every `codebugs` call already shipped in these scripts — `worktree-setup.sh:111`
(`if command -v … ; then`), `:209` (`if codebugs update … ; then`), `:227` (same shape), and
`worktree-finish.sh:1249` / `:1262` / `:1288` / `:1322` (all
`[[ "${SKIP_CHECKS}" != true ]] && [[ -x … ]]` with a `|| echo "⚠ … (non-fatal)"` tail).

The rule makes the worst Round-2 defect — killing `worktree-finish.sh` **after** `merge --no-ff`
has landed on main — impossible by construction rather than by care.

**Precondition on both scripts (verify before implementing):** the claim written by
`worktree-setup.sh` and the release issued by `worktree-finish.sh` must reach the **same** tracker.
`db.connect()` walks up from cwd for `.codebugs/`, and follows a `.git` *file*'s `gitdir:`/`commondir`
pointer, so a call from inside a linked worktree resolves to the main repo's DB. Both scripts run
with cwd = `REPO_ROOT` in normal use (`worktree-finish.sh:1317` states this explicitly for its own
block). A shell test asserts setup-then-finish round-trips through one DB.

---

### 15.1 Commit **S0** — ancestry filter. Independent of the ledger. Land it first, or alone.

**This closes the merged-but-undeleted false-positive class with no new state, and it is what Round 2
wrongly claimed only a claim ledger could do (R5).** It has no dependency on `codebugs` at all.

**Insert into `/home/faxik/w/autosorter/tools/worktree-setup.sh` between `:88` and `:90`**
(`:88` is `        | grep -vx "${BRANCH_NAME}" || true)`; `:89` is blank; `:90` is
`    if [[ -n "${others}" ]]; then`):

```bash
    # Integration is `merge --no-ff` (worktree-finish.sh:1198) and nothing ever
    # deletes a branch — `git branch -d/-D` has ZERO occurrences in that script,
    # which removes the worktree (:1338) and leaves the ref forever. So every
    # INTEGRATED branch is still listed by `git branch`, and refusing on it is a
    # pure false positive — the one that trains people to pass --allow-duplicate
    # by reflex. `--merged` is exact here *because* integration is --no-ff: an
    # integrated branch is a strict ancestor of the base.
    if [[ -n "${others}" ]]; then
        _merged=$(git -C "${REPO_ROOT}" branch --merged "${BASE_BRANCH}" \
            --format='%(refname:short)' 2>/dev/null || true)
        if [[ -n "${_merged}" ]]; then
            others=$(printf '%s\n' "${others}" \
                | grep -vxF -f <(printf '%s\n' "${_merged}") || true)
        fi
    fi
```

`BASE_BRANCH` is in scope — defined at `:39` (`BASE_BRANCH="${2:-HEAD}"`), well before `:86`.
Probed this run: `grep -vxF -f <(…) || true` survives `set -euo pipefail` on both a partial match
(one line remains) and a total match (empty output, script survives). The `|| true` is on the
**pipeline**, not inside a loop body — that is exactly the placement error that made Round 2's finish
block fatal (R8).

**Effect:** `others` retains only branches that are *not* yet merged. `:90-105`'s refusal then fires
only on genuine work in flight. Roughly **+12 lines, no deletions, no new dependencies.**

---

### 15.2 Commit **S1** — the claim gate in `worktree-setup.sh`

#### (a) Helpers + abort trap — insert immediately after `:81` (`_claim_ids=""`), before `:82` (`for cb in ${CB_IDS}; do`)

```bash
# --- entity-claim gate (codebugs) -------------------------------------------
_claim_one() {                      # returns the CLI's exit code verbatim
    codebugs claim "$1" --holder "${BRANCH_NAME}" --holder-kind branch \
        --repo "${REPO_ROOT}" --note "worktree-setup ${SLUG}"
}

# Give back any claim taken below if setup dies before the worktree exists.
#
# EXIT, not ERR: an explicit `exit` does NOT fire an ERR trap — probed this run
# (`trap "…" ERR; exit 1` printed nothing; the same script with EXIT printed).
# Installed BEFORE the loop so an `exit 1` on the SECOND card still releases the
# FIRST card's claim; Round 2 installed it after the loop and missed exactly
# those exits.
_release_claims_on_abort() {
    local cb
    for cb in ${_claim_ids}; do
        codebugs release "${cb}" --holder "${BRANCH_NAME}" \
            --reason "worktree-setup aborted" >/dev/null 2>&1 || true
    done
}
trap _release_claims_on_abort EXIT
```

`local cb` shadows the enclosing loop's `cb`. `|| true` on the release keeps a failing trap from
altering the script's exit status. `for cb in ${_claim_ids}` over an empty string is zero iterations
under `set -u` — probed.

#### (b) Replace `:107-135` — the whole `# Registry check + claim` block, from its comment through its closing `fi`

This is the **one permitted fatal call**. It sits inside the `for cb` loop that closes at `:136`,
therefore before `mkdir` at `:139` and before `git worktree add` at `:143`.

```bash
    # Claim the card. THIS IS THE GATE, and it is the ONLY new codebugs call in
    # either script permitted to be fatal: it runs before `git worktree add`
    # (:143), so a refusal costs a re-run and nothing irreversible has happened.
    # Every other new codebugs call added by this change is `|| true` or
    # `if`-guarded, matching every call already shipped in these scripts.
    #
    # This also REPLACES the old `codebugs get | sed` pre-read: "only open cards
    # are flipped, a follow-up branch on a fixed card must not silently reopen
    # it" (the rule at :205-207) is now the claim's `entity_terminal` outcome,
    # decided inside one transaction instead of by a shell check-then-act.
    #
    # AUTOSORTER_SETUP_NO_CLAIM lets tests exercise the pure-git guard above
    # without writing to the real findings database.
    if command -v codebugs >/dev/null 2>&1 && [[ -z "${AUTOSORTER_SETUP_NO_CLAIM:-}" ]]; then
        # `|| _rc=$?` suppresses `set -e` for this command and captures the code.
        # An UNGUARDED call here would kill the script before `case` ever ran —
        # probed: `set -e; false; case $? in …` never reaches the case.
        _rc=0; _claim_one "${cb}" || _rc=$?
        if [[ "${_rc}" == "5" ]]; then          # undetermined: DB contended
            sleep 1
            _rc=0; _claim_one "${cb}" || _rc=$?  # re-test the CODE, not a boolean
        fi
        case "${_rc}" in
            0)  _claim_ids="${_claim_ids} ${cb}" ;;
            3)  echo ""
                echo "  ${cb} is claimed by another holder (named above)."
                echo "  Two sessions building one card is how CB-2431 and CB-2534"
                echo "  were each implemented twice."
                echo ""
                echo "  Inspect:        codebugs who-holds ${cb}"
                echo "  If it is dead:  codebugs release ${cb} --holder <that-holder>"
                echo "  Proceed anyway: re-run with --allow-duplicate"
                [[ "${ALLOW_DUPLICATE}" == "1" ]] || exit 1 ;;
            4)  echo ""
                echo "  ${cb} is already resolved; a follow-up branch must not reopen it."
                echo "  Proceed anyway: re-run with --allow-duplicate"
                [[ "${ALLOW_DUPLICATE}" == "1" ]] || exit 1 ;;
            5)  echo "  ⚠ ${cb}: codebugs stayed contended after a retry; continuing UNCLAIMED." ;;
            *)  echo "  ⚠ ${cb}: could not be claimed (codebugs rc=${_rc}); continuing unclaimed." ;;
        esac
    fi
```

**Outcomes 3, 4 and 5 are handled distinctly** — Round 2 collapsed them into one boolean retry, so a
rival winning during the `sleep` produced a loser that proceeded (R11). The retry now re-reads the
exit code and a still-contended second attempt lands in `5`, not in `0`.

**`_claim_ids` is only appended on rc 0.** With `--allow-duplicate` past a `3` or `4`, the script
proceeds **without** holding the claim, so the abort trap has nothing to give back that it never
took.

#### (c) Disarm the trap — insert immediately after `:143` (`git … worktree add -b …`)

```bash
# The branch now exists and carries the claim; worktree-finish.sh gives it back.
# Disarm HERE, not at the end of the script: a later symlink/testmon failure must
# NOT hand the card back while a real worktree is sitting on disk. The residual
# is stated in §15.4.
trap - EXIT
```

Probed: `trap - EXIT` disarms cleanly and nothing fires on the subsequent exit.

#### (d) Delete `:197-215` — the write-only projection loop and its comment block

`:197-207` is the `# Auto-claim the card now that the worktree exists (CB-2489 fix (c)).` comment.
`:208-214` is the complete `for cb in ${_claim_ids}; do … codebugs update "${cb}" --status
in_progress … done`. `:215` is the blank line before `:216`'s `# Optional: flag codebugs items…`.
Deleting `197-215` inclusive leaves `:195` (`fi`), `:196` (blank), then the `--items` block — a
syntactically complete file with no orphaned `fi`/`done`. **Codex's warning about Round 2's
`:206-212` deletion producing broken shell does not apply to this range; I checked the boundaries
line by line.**

The loop is redundant because the claim at (b) already projected `findings.status='in_progress'`
through `EntityKind.busy_status` (§10), inside the same transaction as the claim. This deletes the
last write-only status write in the script.

**One disclosed behaviour change:** today `AUTOSORTER_SETUP_NO_CLAIM` guards `:111` but **not**
`:208-214`, so the env var suppresses the *read* and still performs the `in_progress` *write*. After
(d), `_claim_ids` is populated only inside the guarded block, so the variable finally means what its
name says: no writes to the findings DB at all.

**Net for S1: roughly +45 / −48 lines.** One check-then-act shell race removed (the `get`-then-`update`
read-then-write). One new obligation added (the abort trap).

---

### 15.3 Commit **S2** — unconditional release in `worktree-finish.sh`

**Insert between `:1333` (the `fi` closing the `[7e/9]` rollup block) and `:1334` (blank, before
`:1335`'s `# 8. Clean up worktree`).**

```bash

# [7f/9] Release entity claims held by this branch.
#
# Deliberately NOT gated on ${SKIP_CHECKS}: releasing a claim is lifecycle
# correctness, not a quality check. A successful `--skip-checks` finish that left
# the claim live would leak ownership BY CONSTRUCTION, which is the exact
# pathology this mechanism exists to remove.
#
# Non-fatal by construction. The merge landed at :1198 and the integration lock
# was released at :1241 — NOTHING after that point may abort this script. Every
# command below is `if`-guarded or `|| true`-guarded.
#
# `${BRANCH}` is defined at :647. (`BRANCH_NAME` does not exist in this file:
# grep count 0. Under `set -u` at :11, referencing it would abort here, i.e.
# AFTER main already moved.)
if command -v codebugs >/dev/null 2>&1; then
    echo ""
    echo "[7f/9] Releasing codebugs entity claims for ${BRANCH}..."
    # `--format ids` prints bare ids, one per line, empty on no matches. The
    # `|| true` is on the command substitution, NOT inside a loop body — a
    # `grep`-parsed JSON pipeline with a misplaced `|| true` is what made the
    # Round-2 version fatal on its own advertised "usually a no-op" path.
    _held=$(codebugs claims --holder "${BRANCH}" --format ids 2>/dev/null || true)
    if [[ -z "${_held}" ]]; then
        echo "  ✓ nothing held (released by the terminal hook, or never claimed)"
    fi
    for _cb in ${_held}; do
        if codebugs release "${_cb}" --holder "${BRANCH}" --no-restore \
               --reason "branch merged" >/dev/null 2>&1; then
            echo "  ✓ ${_cb} released"
        else
            echo "  ⚠ ${_cb}: release failed (non-fatal). Run by hand:"
            echo "      codebugs release ${_cb} --holder ${BRANCH} --no-restore"
        fi
    done
fi
```

**Ordering:** this sits after `[7b/9]`'s `auto-resolve-codebugs.py` (`:1249-1257`), which flips
findings to `fixed` and thereby fires the terminal hook (§11.4) and auto-releases the claim already.
By the time `[7f/9]` runs it is **usually a no-op — and that is the point**: it is the belt for the
cases the trailer did not cover (a branch merged with no `Fixes:` trailer, a card worked but not
resolved). Round 2 said the same thing and then made the no-op path the fatal one; this version
prints a line and exits the block.

**`--no-restore`, deliberately.** The branch merged: work landed. Flipping the card back to `open`
would erase that signal. If `[7b/9]` already moved it to `fixed`, §7.4's `expected = projected_to`
guard would refuse the restore anyway — `--no-restore` makes the intent explicit rather than relying
on the guard.

**Step label `[7f/9]`:** `[7b]`…`[7e]` are taken (`:1249`, `:1262`, `:1288`, `:1322`) and the
`/8`-vs-`/9` denominators are already inconsistent in the shipped file (`:658` says `[1/8]`).
`[7f/9]` follows the local convention.

**Roughly +32 lines, no deletions.**

---

### 15.4 What the adoption does NOT cover — stated, not hidden

**M9 — `--items=CB-N` creates no claim. OUT of scope for v1, by decision.** `worktree-setup.sh`
parses `--items` at `:22` into `ITEMS` and consumes it at `:216-233` — **after** `git worktree add`
at `:143`. A claim taken there would gate nothing: the worktree exists, the branch exists, the agent
is about to start, and a `held_by_other` selects an `echo`. That is precisely the position Round 2's
own analysis called worthless. Claiming `--items` properly requires moving the `--items` loop above
`:143`, which changes the script's structure for a second consumer — a separate change, filed in §19.
No leak results: the finish release (§15.3) is driven by *held* claims, so an unclaimed `--items` id
is simply never released because it was never taken.

**Residual leak, after (c)'s disarm point.** If setup fails *between* `git worktree add` (`:143`) and
the end of the script — a symlink failure, a testmon copy failure — the trap is already disarmed, so
the claim stays. This is deliberate: the alternative (a trap armed to the end) releases ownership
while a real worktree sits on disk, which Codex correctly flagged as clearing too much. The residual
is visible (`codebugs who-holds <id>` names the branch) and recoverable
(`codebugs release <id> --holder <branch>`), and the branch genuinely exists, so the claim is not
lying about anything.

**Not covered at all:** `fix-latest-codebugs/SKILL.md` and any other prompt-level instruction. Those
are advisory, outside the repo, and **do not count as adoption**. I am not counting them.

---

## 16. Testing strategy

`tests/test_claims.py`, own fixtures, no shared `conftest.py` (CLAUDE.md). Concurrency tests follow
the two existing precedents, both of which I opened this run:

- **`tests/test_milestones.py:801-846`** — `TestPullNextConcurrent::test_two_threads_two_connections_no_double_claim`:
  two threads, each opening its own `db.connect(tmp_project)` (the **production** discovery path),
  a `threading.Barrier(2)` at `:817`, asserting uniqueness of the claimed refs at `:844`.
- **`tests/test_sweep.py:754-799`** — `TestConcurrentAdd::test_concurrent_upsert_atomic`: 10 threads,
  raw `sqlite3.connect(db_path, timeout=10.0)` at `:777`, `threading.Barrier(N)` at `:773`, asserting
  exactly one row with `recurrence_count == N` at `:795-798`.

Fixtures copy `tests/test_milestones.py:12-22` verbatim in shape: `tmp_project` =
`db.init_project(str(tmp_path))`, `conn` = `db.connect(tmp_project)`. **File-based, not in-memory** —
every claims test either races across connections or exercises `db.connect()`'s discovery, so the
in-memory fixture is not applicable anywhere in this file.

I follow the **milestones** shape (production `db.connect`) for the headline race and borrow the
sweep test's N-thread stress for the idempotence-under-load test.

### 16.1 Tests that prove the success criteria

| # | Test | Proves |
|---|---|---|
| 1 | 2 threads / 2 connections / barrier → exactly one `claimed`; **the loser's outcome string is `held_by_other` and its response names the winner's full holder triple** | Criterion 1. The existing precedents assert uniqueness only; asserting the loser's *report* is the entire point of this feature |
| 2 | `utc_now` monkeypatched to a constant; two `claim()` calls, same holder → `claimed` then `already_mine`, `touch_count` 1 → 2 | Criterion 2. **Fails on any timestamp-based discriminator**; passes on `touch_count` |
| 3 | 10 threads claiming as the *same* holder (sweep shape) → 1 `claimed` + 9 `already_mine`, final `touch_count == 10`, exactly one live row | Criterion 2 under load |
| 4 | `who_holds` / `held_by` return the right rows; `EXPLAIN QUERY PLAN` on both shows `USING INDEX idx_claims_live` / `idx_claims_holder_live` | Criterion 3, **and** that it stays a point query rather than a fold |
| 5 | A **synthetic third `EntityKind`** declared in the test with `busy_status='working'` over a purpose-built table: claim → project → release, with **zero changes to `claims.py`** | Criterion 4, executed rather than asserted in prose |
| 5b | For every kind in `ENTITY_KINDS` with `busy_status is not None`: `PRAGMA table_info` contains `{id,status,updated_at}`; a `set_status` to the declared value round-trips byte-identically then rolls back; `busy_status not in kind.terminal` | §10.3's preconditions P1–P3 — **the M7 fix, enforced** |
| 6 | Full claim → release lifecycle on `FR-1`: `projected == false`, `prev_status`/`projected_to` both NULL, requirement status unchanged throughout, **no CHECK violation** | Criterion 5, and the user's ratified decision |
| 7 | `findings.query_findings(status="in_progress")` still returns claimed findings after a projecting claim | Criterion 6, no regression on the existing read |
| 8 | `idle_seconds` grows with a frozen-then-advanced clock; `stale` appears **only** when `stale_after_seconds` is passed and is absent otherwise; `divergent_only=True` returns exactly the claims whose entity is terminal | Criterion 7, at the reduced strength stated in §18 |
| 9 | `tests/test_milestones.py:801-846` passes **unmodified**; `pull_next` behaviour is byte-identical to today | Criterion 9, and that the deferral is real |

Criterion 8 (milestones convergence) is satisfied by a **stated convergence plan**, per the brief's
own wording (`00:180-181`, *"either subsumed or has a stated convergence plan"*). The plan is §19's
first row. There is no code and therefore no test, and I am flagging that as the weakest point of the
delivery rather than dressing a doc paragraph as coverage.

### 16.2 Tests that exist because a specific defect was found

| # | Test | Defect it locks down |
|---|---|---|
| 10 | Open a transaction, call every `_*_core`, assert `conn.in_transaction` is still `True` afterwards | §5.2 — a core function that commits fails here |
| 11 | `with db.txn(conn): with db.txn(conn):` — inner yields `False`, no `OperationalError`, exactly one commit | §5.1 reentrancy |
| 12 | Force an exception inside `db.txn` after SQLite has auto-rolled back; assert the **original** exception surfaces, not `cannot rollback - no transaction is active` | §5.1's guarded ROLLBACK |
| 13 | `busy_timeout=0` + a write lock held by a second connection → `claim()` returns `outcome="undetermined"`, **no exception escapes** | §8.3. Without this test the fourth outcome is documentation |
| 14 | Inject a non-contention `OperationalError` (`sqlite_errorcode = 1`) → it **propagates** and is not reported as `undetermined` | §9, the other direction |
| 15 | **Claim `FR-1` while its status is `implemented` → `entity_terminal`.** Repeat with `project=False` on a `fixed` CB → also `entity_terminal` | **M1** — the guard Round 2 made unreachable for every requirement and every `--no-project` claim |
| 16 | Claim CB-1, `update_finding(status="fixed")` → auto-released, and the row is **still there** with `released_at` set and `release_reason='terminal:fixed'` | §11.4, and that history has a home |
| 17 | Claim `FR-1`, `update_requirement(status="implemented")` → auto-released with `release_reason='terminal:implemented'` | §11.2 — **both** writers fire the hook, not just findings |
| 18 | Spy hook counting invocations: `update_finding(status="fixed")` on a finding already `fixed` fires **zero** hooks; `update_finding(notes=…)` with no status fires zero; a real `open→fixed` fires one | **M2** — the `changed` guard, all three arms |
| 19 | Spy hook: a claim followed by a release fires **zero** status-change hooks | §10.2's no-recursion invariant |
| 20 | Claim as `(br-a, branch, /repo/x)`; re-claim as `(br-a, branch, /repo/y)` → `held_by_other`, response names `/repo/x`. Then `(br-a, agent, /repo/x)` → `held_by_other`. Then a `holder_repo=None` claim renewed with `None` → `already_mine` | **M3**, all three arms incl. the NULL case (mirrors probe P1) |
| 21 | Claim (projects `open→in_progress`) → holder sets `fixed` → `release()` → status stays `fixed`, `status_restored == false` | §7.4 — release never resurrects finished work |
| 22 | Claim → release → **claim by a different holder** → succeeds with a new `claim_id`; the old row survives with `released_at` set; live count is 1 at every step | §4.1 — soft delete, no release/reclaim race |
| 23 | Release with a holder that does not hold it → `not_yours` naming the real holder; release when nothing is claimed → `not_claimed`; **neither writes anything** | §7.4 |
| 24 | Source-tree ratchet: `grep -rn 'BEGIN' src/codebugs/` yields only `db.txn` plus the two known pre-existing sites (`merge.py:242`, `milestones/capacity.py:182`) | §5.1's rule, ratcheted so the deferred refactor can only shrink the allowlist |
| 25 | CLI exit codes via `subprocess`: `claim` twice with different holders → `0` then `3`; on a `fixed` finding → `4`; `who-holds` on an unheld id → `3`; `claims --format ids` with no matches → **rc 0 and empty stdout** | §12.2 — the shell contract |
| 26 | `claims --format ids` output is bare ids, one per line, no header, and round-trips into `release` | §15.3's loop, which is only correct if this holds |

Tests 25 and 26 matter more than their size suggests: **the exit codes and the `ids` format are what
the shell gates on**, so an unasserted exit code is an unasserted gate.

### 16.3 Shell tests

The shell diffs are not covered by pytest. Three manual/scripted checks, run against a throwaway
clone before merge:

| # | Check | Passes if |
|---|---|---|
| S-a | `AUTOSORTER_SETUP_NO_CLAIM=1 worktree-setup.sh …` on a card with an existing **merged** branch | S0's filter drops it; setup proceeds; no `codebugs` write occurs |
| S-b | `worktree-setup.sh` for a card already claimed by another holder | rc 1, **no worktree created**, no branch created, and the earlier card in a two-card branch name is released by the EXIT trap |
| S-c | setup → finish round trip | the claim is created against the main repo's `.codebugs/` and `[7f/9]` releases it (or reports "nothing held" because `[7b/9]` already did) |

S-b is the one that must be run: it is the only end-to-end proof that the gate aborts **before**
`git worktree add` and that the abort trap covers an early `exit`.

---

## 17. Effort — split module vs delivery

### Module — **M**

| Component | Lines | Risk |
|---|---|---|
| `claims.py` (schema, 2 core + 2 public + 3 read fns, 5 MCP tools, 4 CLI verbs, hook) | ~200 | Low — the substrate is executed and proven |
| `db.txn` | ~25 | Low |
| `db.register_status_change_hook` / `run_status_change_hooks` | ~30 | Low — copies `register_post_add_hook` (`db.py:178-193`) |
| `db.connect` `busy_timeout` | 1 | Nil — own behaviour-neutral commit, lands first |
| `entities.py`: `busy_status` field + `set_status` | ~19 | Low, but it changes a documented module rule (§3.2) |
| `findings.py` + `reqs.py` hook firing with the `changed` guard | ~16 | **Medium** — it edits the hot path of the two most-used write functions in the repo |
| Wiring (`db.py:487`, `server.py`, `cli.py`) | 3 | Nil |
| `tests/test_claims.py` | ~250 | Low |
| **Module subtotal** | **~544** | of which **~250 is test code** |

### Delivery — **M/L**, and this is where the risk lives

| Component | Lines | Risk |
|---|---|---|
| `worktree-setup.sh` S0 (ancestry filter) | +12 | Low — pure git, independently landable |
| `worktree-setup.sh` S1 (trap + gate + disarm + deletion) | +45 / −48 | **High.** It is another repository, `set -euo pipefail`, and the one fatal call lives here. Four Round-2 defects were all in shell |
| `worktree-finish.sh` S2 (release) | +32 | Medium — post-merge position means a defect here damages main |
| **Delivery subtotal** | **~89 added, ~48 removed** | |

**Total ≈ 585 changed lines**, against the Judge's arithmetic of ~550. The ~35 delta is honest and
locatable: the hook seam's docstrings (~10), the shell comments explaining *why* each guard exists
(~15), and §10.3's precondition test (~10). I would not trim any of the three — the shell comments
in particular are what stop the next editor from re-introducing R7–R11.

### Commit order

| # | Commit | Depends on |
|---|---|---|
| 1 | `db.connect`: explicit `PRAGMA busy_timeout=5000` | — (behaviour-neutral, lands alone) |
| 2 | `db.txn` + the no-plain-`BEGIN` ratchet test | 1 |
| 3 | `entities.py`: `busy_status` + `set_status` + §10.3 preconditions test | — |
| 4 | `db.register_status_change_hook` / `run_status_change_hooks` | — |
| 5 | `findings.py` + `reqs.py` fire the hook, guarded by `changed` | 4 |
| 6 | `claims.py` + `tests/test_claims.py` + wiring | 2, 3, 4 |
| 7 | **S0** — `worktree-setup.sh` ancestry filter | **nothing.** Can land before 1 |
| 8 | **S1** — `worktree-setup.sh` claim gate | 6 released/installed |
| 9 | **S2** — `worktree-finish.sh` release | 6, 8 |

Commits 1–5 are individually revertible and none of them changes observable behaviour except 5,
which adds a hook call that fires against an empty registry until 6 lands. **Commit 7 is deliberately
first-and-independent** — it is the change with the best ratio in the whole design and it should not
wait on anything.

---

## 18. What This Does And Does Not Buy

### 18.1 What it buys

1. **A queryable ownership record.** "Who holds CB-1234" and "what does `agent-7` hold" are two
   indexed point queries against a table whose meaning is unambiguous (`released_at IS NULL` == live).
   This is success criterion 3 (`00:175`) and it is what the user approved in Round 1: *"the agent
   (or the user) asking the tracker 'is this taken?' before starting work"* (`CHECKPOINT-r1:24`).
   Today there is no such thing — `findings.status='in_progress'` is a write-only field with ~41
   stale rows (`worktree-setup.sh:120`) that no code path gates on.

2. **Mutual exclusion at the database, from the first run.** The unique partial index makes two
   concurrent claimants for one entity mutually exclusive, and the loser is *told* it lost and *by
   whom* — not just refused. Both Round-2 adversaries executed this against real SQLite independently
   and could not break it: no release/reclaim race, no double-release, no path to two live rows.

3. **Closure of one TOCTOU window that provably exists in shipped code.** The unserialized gap
   between `worktree-setup.sh:86`'s ref-namespace read and `:143`'s ref-creating write cannot be
   closed by git, because the two competing branch names differ by construction. The claim gate
   closes it. **This is the ledger's entire remaining justification** (§1.1).

4. **A claim that survives the shell.** The gate moves the ownership decision from a `codebugs get |
   sed` check-then-act pipeline (`worktree-setup.sh:112-113`) into a `BEGIN IMMEDIATE`. It removes a
   race from the live call site rather than adding a mechanism beside it.

5. **Automatic clearing on terminal status, with an audit record.** A `Fixes: CB-1234` trailer
   already flows through `update_finding`; the hook makes it also close the claim, in the same
   transaction, leaving `release_reason='terminal:fixed'` behind as evidence.

6. **Requirements get ownership without a schema fight.** Criterion 5 (`00:177`) reads *"Requirements
   are covered despite having no busy status"* — the brief's own phrasing already anticipated the
   answer the user gave. Requirements get claim, release, and query; `reqs.py:22-23`'s CHECK
   constraint is untouched.

7. **A third entity kind by declaration**, with its preconditions written down and executed (§10.3)
   rather than assumed.

### 18.2 What it does NOT buy

1. **It does not prevent either recorded incident.** CB-2431 (~40 minutes apart) and CB-2534
   (two slugs, 2026-08-04) are **sequential**, and the shipped git guard at `:86-105` already refuses
   the second launch in both. **The subclass the ledger prevents — two setups whose `:86` scans both
   precede either `:143` — has never been observed.** It is a real window in real shipped code; its
   incidence is unmeasured. §1.5 specifies the ten-minute test that would convert that inference into
   a fact, and it should be run.

2. **It does not close the false-positive class the design was originally sold on.** `git branch
   --merged` does that, with no table, no module, no history, and effect on day one. That is commit
   S0 (§15.1), it is independent of everything else here, and **it should ship whether or not the
   ledger does.** Round 2's claim that only a claim ledger could distinguish a stale branch from work
   in flight was false (R5).

3. **It does not tell you whether a claimant is alive.** The git liveness predicate is dead on two
   counts (§1.2) and the verifier is deferred. What v1 offers for criterion 7 (`00:179`, *"stale
   ownership is visible and recoverable without a background process"*) is: `idle_seconds` on every
   row, an opt-in `stale_after_seconds` threshold the *reader* chooses, a `divergent` flag for claims
   on already-terminal entities, and a manual `release`. **That is "visible and recoverable"; it is
   not "detectable".** Nothing distinguishes a dead agent from a slow one. This is the criterion this
   delivery meets least well, and it meets it by reporting rather than by knowing.

4. **It does not fix the ~41 stale `in_progress` findings.** The table starts empty on purpose; no
   backfill. Those remain a `findings.status` cleanup task.

5. **It does not have a second in-repo consumer.** `pull_next` integration is deferred, so the only
   real gate is in another repository's shell script. If `worktree-setup.sh` is not the workflow
   being used, this delivery's gate is unreached and it degrades to an advisory record that agents
   must be told to call. That is the risk the "correct primitive dying unwired" precedent
   (`00:166-167`) exists to warn about, and deferring `pull_next` makes it worse, not better. I flag
   it as the single largest delivery risk.

6. **It does not make the ledger self-healing.** Hook failures are swallowed. The degraded state
   (a live claim on a terminal entity) is *visible* via `divergent` and *manually* recoverable. It is
   not automatically repaired, and I am not claiming the clearers keep the table trustworthy — only
   that when they fail, the failure is queryable.

7. **It changes nothing about `merge.py` or `milestones/capacity.py`.** Their hand-rolled
   `BEGIN IMMEDIATE` blocks (`merge.py:242`, `capacity.py:182`) stay exactly as they are.

---

## 19. Deferred Follow-Ups

Each is its own commit and its own codebug. None is a dependency of anything in §17's commit order.

| # | Item | Why deferred | What it needs |
|---|---|---|---|
| D1 | **`pull_next` / `release_item` integration** (criterion 8's code half) | Deferring removes four independently-found defects at once, including pseudocode that **cannot run**: it passes `holder=agent_id` while `release_item`'s variable is `agent` (verified this run: `milestones/capacity.py:234-235` reads `item = _get_item_by_ref(…)` / `agent = item.get("assigned_agent")`). Shipping a broken second consumer is worse than shipping one | A **public** ambient-transaction API on `claims` (CLAUDE.md forbids `capacity.py` calling `_claim_core`), an `item_kind='external'` branch (those items have no resolvable entity id), and a held-entity skip policy for `pull_next`'s candidate loop |
| D2 | **`expected_status` + `changed`** on `update_finding` / `update_requirement` (+ MCP + CLI) | Orthogonal to ownership — §11.2's `changed` guard is already written to be correct when this lands | Its own design pass; it is a generic CAS for arbitrary transitions, not a claims feature. **Note: this was the literal form of the user's original question.** It is deferred, not dropped — the claim outcome vocabulary (§8) answers the same need for the claim case |
| D3 | **`steal`** (explicit opt-in ownership transfer) | On my own Round-2 cut list; no caller in v1 | Two statements in one transaction, with `prev_status` **carried over from the victim row** (re-reading it would make the thief's release restore to the victim's projected status, permanently pinning the finding), and `expected_holder` as a required CAS argument |
| D4 | **Staleness verifier + `claims audit [--prune]`** | The git predicate must be re-based onto **ancestry** before any of it is written (§1.2). Deferring also removes the MCP-subprocess concern (up to 2N sequential git processes per audit) and the pre-worktree prune hazard (a concurrent `--prune` releasing a valid in-flight claim taken at §15.2(b) *before* its branch exists at `:143`) | An ancestry predicate (`git merge-base --is-ancestor` / `git show-ref --verify --quiet`, **never** `git rev-parse --verify` — it accepts `refs/heads/main~1`), a uniform cwd-failure mapping (a file-as-cwd probe raised an uncaught `NotADirectoryError` through `db.git_rev_parse`, `db.py:210-226`), a bound on rows audited, and a rule that `--prune` never touches a claim younger than the setup window |
| D5 | **`claim-history` CLI / `claims_history` MCP** | Data is already there (soft delete); only the surface is missing | Trivial once wanted |
| D6 | **Retention** (`DELETE FROM entity_claims WHERE released_at < ?`) | ~200 bytes/row, tens per week. Round 2 promised a `prune --before` verb in prose and shipped none (R16) | Add the verb when the table's real growth is known |
| D7 | **`codebugs get` gains a `claim` block; `codebugs summary` gains a claims line** | Ergonomics, not correctness — but it is the fix for §10.4's disclosed cost (a projected status change with no visible explanation) | ~4 lines each, plus a seam so `findings.py` still does not import `claims` |
| D8 | **`holder_kind='process'`** | Cut from the CHECK list (§4). Adding a value later needs a table rebuild, the same shape as `reqs.py:53-95` | A real use case |
| D9 | **`merge.py` + `milestones/capacity.py` refactor onto `db.txn`** | Mechanical and behaviour-identical; touches modules claims never uses. §16.2's test 24 ratchets the allowlist so this can only shrink it | Covered by the existing race test at `tests/test_milestones.py:801-846` |
| D10 | **`release_item` atomicity fix** | A real bug, and **independent of claims** by the design's own admission. It deserves its own commit, not a ride-along | Its own investigation |
| D11 | **`--items=CB-N` claim coverage** (M9) | The `--items` block runs at `worktree-setup.sh:216-233`, **after** `git worktree add` at `:143`, so a claim there gates nothing (§15.4) | Moving the `--items` loop above `:143`, which restructures the script for a second consumer |
| D12 | **`meta` read-modify-write lost update** in `update_finding` (`findings.py:265-285` re-serialises the whole JSON blob) | Correctly identified in Round 2 and correctly scoped out | File as a codebug |
| D13 | **`git branch -d "${BRANCH}"` in `worktree-finish.sh`** after a successful merge | Not part of this design at all, but found by it: branches accumulate forever and each one blocks every future branch for its card via the `:86-88` guard | Its own commit. **Note the interaction:** doing D13 makes S0's ancestry filter mostly redundant (a deleted branch is not in `others` at all). Do S0 first — it is safe, reversible, and needs no decision about whether deleting branches is acceptable here |
| D14 | **CLAUDE.md stale-fact fix:** the `blockers.py` debt bullet cites `db._row_to_dict()` / `reqs._row_to_dict()`; neither exists — it is public `db.row_to_dict` (`db.py:229`) | Unrelated, found in passing | One-line edit |

---

## 20. Pre-implementation gates and definition of done

### 20.1 Gates — do these before writing code

1. **Run the concurrency probe from §1.5.** Two `worktree-setup.sh` invocations, same card, different
   slugs, launched concurrently; observe whether both create worktrees. Ten minutes. If the window is
   practically unreachable in this workflow, the ledger's justification thins further and the user
   should hear that *before* implementation, not after. **This is the one open empirical question in
   the design.**
2. **Land commit 7 (S0) regardless of the outcome.** It is independent, it is a few lines of pure
   git, and it is strictly better than the ledger at the job the ledger was originally sold on.

### 20.2 Definition of done

- All 26 tests in §16.1/§16.2 pass; `uv run python -m pytest tests/ -v` green with no regressions,
  specifically including `tests/test_milestones.py:801-846` **unmodified**.
- `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.
- `codebugs --mode claims claim --help` works, proving mode isolation and the `cli.py:49` wiring.
- `codebugs-mcp --mode claims` starts, proving the `server.py` wiring.
- Shell checks S-a, S-b, S-c (§16.3) run against a throwaway clone. **S-b is mandatory** — it is the
  only end-to-end proof that the gate aborts before `git worktree add`.
- The five CLAUDE.md amendments from §14 are written.
- Each of D1–D14 is filed as a codebug with a one-line rationale, so the deferral is a record rather
  than an omission.

### 20.3 The single thing I am least sure of

**That the gate will be reached.** The mechanism is proven; the delivery hangs on one shell script in
another repository. If `worktree-setup.sh` is not the path being used for a given piece of work, the
claim is never taken, the ledger stays empty for that work, and the ownership record is advisory
again — which is the precedent the brief's Fact 2 exists to warn about (`00:166-167`, *"a correct
primitive dying unwired"*). Deferring `pull_next` means the first delivery has **exactly one
consumer, in another repo**. If the user would rather have the second in-repo consumer than the
smaller diff, D1 moves back to IN with a full correctness pass attached — and that is a decision
worth making explicitly rather than inheriting from a scope ruling.

