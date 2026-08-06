# `entity_claims` — Final Design

**Status:** SPEC. Normative. An implementer needs this document and `FINAL-PLAN.md`, and nothing else
from the council artifacts.
**Date:** 2026-08-06

This document supersedes every earlier artifact in
`docs/superpowers/plans/design-council-entity-claims/`. Where it disagrees with any of them, this one
is correct. Code blocks are the source of truth; where prose and a code block appear to differ, the
code block wins and the prose is a defect.

---

## 1. Design Council Session

**Problem:** the tracker cannot answer "is this entity taken, and by whom?"

| | |
|---|---|
| **Rounds** | 3 competitive rounds + 2 independent final verifications + this fold pass |
| **Date** | 2026-08-06 (Rounds 1–3 and both verifications ran 2026-08-05 → 2026-08-06) |
| **Artifacts** | `00-problem-brief.md` … `11-final-verifier.md`, `CHECKPOINT-r1.md`, `CHECKPOINT-r2.md` |

**Team**

| Role | Artifact | Contribution that survived |
|---|---|---|
| Architect A | `01-architect-a.md` | Round-1 proposal set |
| Architect B | `02-architect-b.md`, `07-architect-r2.md`, `10-architect-r3.md` | The claim-ledger design this document is built from |
| Architect C | `03-architect-c.md` | Round-1 proposal set |
| Researcher | `04-research.md` | Prior art on lease/claim ledgers and their failure modes |
| Adversary (Opus) | `05-adversary-r1.md`, `08-adversary-r2.md` | Killed the git-liveness predicate; "empty at launch" |
| Adversary (Codex/Sol) | Round-1 and Round-2 attacks, quoted in `06-judge-r1.md` / `09-judge-r2.md` | The four shell FATALs; the TOCTOU vindication; the holder-triple defect |
| Judge | `06-judge-r1.md`, `09-judge-r2.md` | Scope ruling; the one-fatal-call rule; the ancestry replacement |
| Final verifier (Opus) | `11-final-verifier.md` | Re-executed every probe; applied the shell diffs; found the label, retraction and ambient-transaction defects |
| Final verifier (Codex/Sol) | scratchpad `codex-final-result.md` | Found the release-authorization hole, the aliased-deferral regressions, and the response-contract split |
| **User** | `CHECKPOINT-r1.md`, `CHECKPOINT-r2.md` | Overrode the Judge's "build almost nothing"; ratified the requirements decision |

Both final verifiers returned **SPEC-READY WITH NAMED FIXES**. Their eleven errata are folded into
the normative text below; they are not listed as future work.

### 1.1 A note on labels

Earlier rounds used two defect-numbering schemes (`FATAL-n`/`MEDIUM-n`, then `S1–S4`/`M1–M9`). Both
were invented by the party writing them up, and the second was falsely attributed to the Judge and
the adversaries — it appears in none of their artifacts, `S1` and `S2` each meant two different
things, and two of the `M` labels were never defined. **This document uses no defect-label scheme at
all.** Every defect is named by what it is.

`S0`, `S1`, `S2` below are the names of three **shell commits** (§6.1–§6.3). They label commits,
nothing else.

---

## 2. Problem Statement

`findings.status = 'in_progress'` is the only ownership-ish signal the tracker has, and it is a
write-only field: `worktree-setup.sh` writes it and nothing reads it to gate anything. The user's own
post-mortem calls it *"a WRITE-ONLY field that nothing reads"* (`/home/faxik/w/autosorter/tools/CLAUDE.md:10`).
Roughly 41 cards sit `in_progress` and a known share of those are stale, per the script's own comment
at `worktree-setup.sh:120`. The field therefore cannot be used as a gate: refusing on it would refuse
mostly on garbage.

Two consequences:

1. **No one can ask "is this taken?"** There is no record of who holds an entity, so an agent (or a
   human) starting work cannot check, and a second agent can start the same card. This is the
   consumer the user named in `CHECKPOINT-r1.md` and the reason this ships.
2. **The shipped duplicate-work guard is check-then-act.** `worktree-setup.sh` reads the branch
   namespace at `:86-88` and creates a branch at `:143`. Nothing serializes that window — there is no
   `flock` in the file. Two setups launched concurrently for the same card **with different slugs**
   both observe `others=""` and both proceed, and git cannot rescue it: the two branch names differ
   by construction, so `git worktree add -b` has no ref collision to detect.

### 2.1 What the evidence supports, at its real strength

Two duplicate-implementation incidents are on record. The entire evidentiary base is one comment in
`worktree-setup.sh:58-71`, quoted here verbatim (opened this run) so no later sentence can drift from
it:

```
# The check above matches an exact worktree PATH, so two different slugs for the
# SAME card pass it. That is not hypothetical: `fix-cb-2534-debug-rescue-scope`
# and `fix-cb-2534-2417-documents-router-scope` were built in parallel on
# 2026-08-04, and CB-2431 before them for ~40 minutes. Both times the card was
# already `in_progress` — but that is a WRITE-ONLY field, read by nothing, so it
# stopped no one.
```

**Neither recorded pair has been shown to be concurrent.** The record gives *"~40 minutes"* for
CB-2431 and *"built in parallel on 2026-08-04"* for CB-2534, and "in parallel" describes parallel
*work* — it does not establish that two `worktree-setup.sh` invocations overlapped inside the
sub-second `:86`→`:143` window. Both recorded pairs are separated in time on the only evidence
available. **The shipped guard already refuses the sequential form of both**, and it stopped neither
only because the thing it consulted was the write-only `in_progress` field.

> **Note on this wording.** One final verifier's erratum asked this document to state that *each*
> recorded pair was "approximately 40 minutes apart". The source above carries that figure for
> CB-2431 only; extending it to CB-2534 would assert something the record does not say. The
> erratum's substance — that no recorded incident is established as concurrent, that the sequential
> form of both is already refused by shipped code, and that no sentence anywhere may imply otherwise
> — is stated here in full, and every derived sentence in this document (§4.3, §6.2, §7) is written
> from it.

The narrowed, correct statement — this is the Judge's ruling at `09-judge-r2.md:102-103`, and it is
stated here once so that no other sentence in this document contradicts it:

> The shipped git guard refuses the **sequential** form of a duplicate launch, which is what both
> recorded incidents were. It does **not** close the **concurrent** form, because the two branch
> names differ by construction. The ledger closes the concurrent form. That form has never been
> observed.

So the honest pitch is:

> The tracker has no way to answer "is this taken?". This adds a queryable ownership record with an
> atomic gate. The gate closes a TOCTOU window that provably exists in shipped code and that has not
> yet been observed to fire.

The mechanism is sound and was executed against real SQLite by two model families. **The incidence is
unmeasured**, and §9 specifies the measurement.

---

## 3. Chosen Approach

A new domain module, `src/codebugs/claims.py`, owning one table, `entity_claims`, with:

- **Soft-delete ledger.** `released_at IS NULL` means live. Closed rows are retained, so history
  exists from day one.
- **A unique partial index** `ON entity_claims(entity_id) WHERE released_at IS NULL` — at most one
  live claim per entity. This is the mutual-exclusion primitive, and it needs **zero history**: it
  works against an empty table on the first run.
- **An idempotent upsert** whose `DO UPDATE … WHERE` compares the full NULL-safe holder triple, so a
  replayed identical call converges on `already_mine` and can never double-claim.
- **A five-value outcome vocabulary** for `claim` and a four-value one for `release`, surfaced to
  shell callers as **exit codes**, so a `set -euo pipefail` script can gate without parsing output.
- **Optional per-kind status projection**, declared as one field on the existing frozen `EntityKind`
  descriptor. Findings project to `in_progress`; requirements declare nothing and never project.
- **A status-change hook seam** in `db.py`, symmetric with the existing `register_post_add_hook`, so
  a finding moving to a terminal status auto-releases its claim inside the same transaction.
- **Adoption in `worktree-setup.sh` / `worktree-finish.sh`**: a claim gate placed **before**
  `git worktree add`, and an unconditional release after the merge.

Plus one commit that is independent of all of the above and should ship regardless: **S0**, a
`git branch --merged` filter in the setup guard (§6.1).

---

## 4. Design Rationale

### 4.1 The user overrode the Judge

The Round-1 Judge recommended **building almost nothing** (~75 lines: `expected_status` + `changed` +
`--append-note`), on a proportionality argument: no consumer for an ownership record had been
demonstrated. The Judge explicitly named the condition under which that argument collapses — a real
consumer the council never surfaced.

The user supplied it (`CHECKPOINT-r1.md`), verbatim: **"Настоящее поле — строим B1"**, with the
preceding clarification *"«Кто держит сущность» может быть достаточным на этом этапе, это кажется
верным."* The consumer is **the agent (or the user) asking the tracker "is this taken?" before
starting work**, and it is accepted as a sufficient goal in its own right.

**The proportionality argument is therefore overridden by the user, knowingly and on the record.**
The Judge's *factual* findings are not overridden and carry through unchanged — in particular that
the recommended change would have prevented neither recorded incident, and that the binding
constraint at the live call site is signal precision, not atomicity.

### 4.2 Rejected: the git-liveness predicate (Round 2's trustworthiness argument)

Round 2 argued the ledger earns its place by making ownership *trustworthy*, via a predicate that
asks git whether a holder's branch still exists. **Dead on two independent counts:**

1. `worktree-finish.sh` contains **no** `git branch -d/-D/--delete` anywhere. It removes the worktree
   and leaves the ref. A branch-existence check therefore returns "live" for every branch ever
   created, forever — it discriminates nothing in the only deployment that would use it.
2. The predicate is wrong *as written*, not merely as implemented. `db.git_rev_parse` runs
   `git rev-parse <ref>` with no `--verify` — and adding `--verify` does not fix it, because
   `git rev-parse --verify refs/heads/main~1` **returns a SHA**. `--verify` guarantees a single
   revision, not an exact ref. If pure existence is ever needed, the correct primitive is
   `git show-ref --verify --quiet "refs/heads/${b}"`.

**Replacement: ancestry, not existence.** Integration is `merge --no-ff` (`worktree-finish.sh:1198`),
so every integrated branch is a strict ancestor of the base, and `git branch --merged` /
`git merge-base --is-ancestor` separates merged branches from work in flight exactly, **with zero new
state**. The predicate stays dead in v1; the verifier that would have used it is deferred (§10, D4),
and when it lands it must be built on ancestry.

### 4.3 Rejected: "only a claim ledger can tell a stale branch from work in flight"

That was Round 2's central sentence and it is **false**. The same one-line `--merged` filter inside
the existing guard loop closes the merged-but-undeleted false-positive class **without the ledger** —
no table, no module, no history, no exit-code contract, effective on the first run. It is specified
as commit **S0** (§6.1) and it is deliberately structured to land independently. *If the ledger were
cancelled tomorrow, S0 should still ship.* It is strictly better than the ledger at the job the
ledger was originally sold on.

What survives as the ledger's justification is **the atomic gate, and only the atomic gate** (§2, §3).

### 4.4 Rejected: `entity_id` as primary key

`entity_id TEXT PRIMARY KEY` structurally forbids more than one row per entity, and SQLite cannot
drop or replace a primary key with `ALTER TABLE` — Round 1's claim that this was "a strict column
subset upgradable by `ALTER TABLE`" was false. `claim_id TEXT PRIMARY KEY` plus the partial unique
index gives **the identical exclusion guarantee** while allowing unbounded *closed* rows per entity.
Verified by execution: after a soft release, a different holder inserts a new row with a new
`claim_id`, the old row survives with `released_at` set, and the live count stays exactly 1
throughout.

### 4.5 Rejected: timestamps as the new/renewed discriminator

`types.utc_now()` formats to whole seconds. Two calls inside the same second produce equal strings, so
a `claimed_at == renewed_at` discriminator misreports a retry as a fresh claim — which is exactly what
an agent retrying on a 250 ms loop does. `touch_count` is a monotone integer incremented by the upsert
itself and is clock-independent. `utc_now` is **not** modified: changing a timestamp format used
across the codebase to fix one discriminator is the wrong trade.

### 4.6 Rejected: routing projection through `update_finding`

`findings.update_finding` calls `conn.commit()`. Routing the projection through it would commit the
status change independently of the claim, so a later failure would leave a projected status with no
claim. `EntityRef.set_status` exists precisely so the claim and the projection land in one
transaction or neither.

### 4.7 Rejected: a `manage_txn: bool` flag on the public functions

A boolean puts the hazard in the call site's hands; two names (`claim` / `_claim_core`) put it in the
reader's face. The core layer never commits and never opens a transaction; the public layer owns both.

### 4.8 Rejected: `REFERENCES findings(id)`

`PRAGMA foreign_keys` is OFF, so a `REFERENCES` clause would read as enforced and would not be.
`milestone_items.milestone_id … REFERENCES milestones(id)` is already decorative; a second decorative
constraint is not added. Integrity comes from write-time validation (`EntityRef.of` → `ValueError`,
`.require` → `KeyError`, before any insert) and from house style (`milestones/triage.py` already
catches `KeyError` for a deleted finding rather than relying on cascade).

### 4.9 Rejected (deferred, not dropped): `expected_status` / `changed`

This was the literal form of the user's original question. It is a generic CAS for arbitrary
transitions, orthogonal to ownership, and it is deferred to its own design pass (§10, D2). The claim
outcome vocabulary answers the same need *for the claim case*. §5.10.2's `changed` guard is written now
so that the refusal path is already correct when the CAS arrives, rather than being a defect
introduced by a later commit.

### 4.10 What was cut to keep the delivery honest

`merge.py` refactor; the `release_item` atomicity fix; `steal`; claim history and summary surfaces;
audit/verifier tooling; `pull_next` integration; retention verbs; `holder_kind='process'`. Each is
listed in §10. **None of them is present under an alias.** In particular there is no `include_released`, no
`--all`, no `divergent_only`, no `--divergent`, no `stale_after_seconds`, no `--stale-after`, and no
`stale` / `orphaned` / `divergent` row decorations — history querying and audit tooling are deferred
by binding ruling, and an alias is still the feature.

---

## 5. Detailed Design

### 5.1 Module boundaries

```
src/codebugs/
  claims.py      NEW domain module — schema, 5 public fns, 5 MCP tools, 4 CLI verbs   ~200 lines
  db.py          + txn()                          reentrant transaction helper (§5.3)   ~25 lines
                 + register_status_change_hook / run_status_change_hooks (§5.10)        ~30 lines
                 + PRAGMA busy_timeout=5000 in connect()                                  1 line
  entities.py    + EntityKind.busy_status: str | None = None   (declarative)              1 line
                 + EntityRef.set_status()   the module's FIRST write path               ~18 lines
  findings.py    + fires run_status_change_hooks, conditioned on `changed` (§5.10.2)     ~8 lines
  reqs.py        + fires run_status_change_hooks, conditioned on `changed` (§5.10.2)     ~8 lines
  server.py      SERVER_NAMES["claims"] = "codeclaims"                                    1 line
  cli.py         --mode choices += "claims"                                               1 line
  db.py:487      _ensure_modules_loaded() import list += claims                           1 line
tests/
  test_claims.py NEW                                                                   ~260 lines
/home/faxik/w/autosorter/tools/
  worktree-setup.sh   claim gate + abort trap; delete the write-only projection loop    ~55 lines
  worktree-finish.sh  unconditional release                                             ~12 lines
```

**Dependency direction, stated as a rule:**

```
claims.py   ──imports──▶  db, entities, types, fmt
findings.py ──imports──▶  db          (fires db.run_status_change_hooks)
reqs.py     ──imports──▶  db, entities (fires db.run_status_change_hooks)
```

**No domain module imports `claims`, and `claims` imports no domain module.** `claims.py` registers
its terminal hook into `db`'s registry at module level; `findings.py` and `reqs.py` call the runner
without knowing who is listening. This is the exact shape of the existing `register_post_add_hook`
seam (`db.py:178-190`, runner at `db.py:193`), where `milestones.auto_route_finding` reaches
`findings.add_finding` without `findings` knowing `milestones` exists.

**No module outside `claims.py` calls a `_*_core` function.** The core layer exists for exactly one
in-module reason: the terminal hook runs inside `update_finding`'s already-open transaction and must
not commit (§5.3). When `pull_next` integration lands (§10, D1) it gets a *public* ambient-transaction
API, decided in that commit, not smuggled in here.

**The one CLAUDE.md rule this changes, explicitly:** `entities.py` becomes read **and write**, gaining
exactly one write method, `EntityRef.set_status`. Its own docstring already claims the role —
*"Owns the one sanctioned cross-table read over `findings` / `requirements`"* (`entities.py:4`) — and
the only way to keep "adding a new entity kind is a single entry in `ENTITY_KINDS`" true for
projection is to put the write where the read already is. Cost, stated: `entities.py` grows a second
interpolated-identifier statement beside `_read`'s (`entities.py:86`, which already carries
`# noqa: S608`), guarded the same way — against the frozen `ENTITY_KINDS` tuple
(`entities.py:36-55`, two kinds), never against caller input.

### 5.2 Schema — full DDL

`claims.py`, module-level `CLAIMS_SCHEMA`, registered with `db.register_schema("claims", ensure_schema)`.

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
    touch_count    INTEGER NOT NULL DEFAULT 1, -- monotone; THE outcome discriminator (§5.7)
    note           TEXT NOT NULL DEFAULT '',

    prev_status    TEXT,                       -- pre-claim status, NULL if not projected
    projected_to   TEXT,                       -- status we wrote, NULL if not projected

    released_at    TEXT,                       -- NULL == LIVE. Soft delete.
    released_by    TEXT,                       -- who/what closed it
    release_reason TEXT                        -- 'explicit' | 'terminal:<status>' | 'branch merged'
);

-- THE mutual-exclusion primitive: at most one LIVE claim per entity.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_live
    ON entity_claims(entity_id) WHERE released_at IS NULL;

-- "what does agent-7 hold" — indexed point query, no fold.
CREATE INDEX IF NOT EXISTS idx_claims_holder_live
    ON entity_claims(holder) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_claims_entity ON entity_claims(entity_id);
```

`holder_kind='process'` is deliberately **not** in the CHECK list (§10, D8). Adding a value later
requires the same table rebuild as `reqs.py`'s `_migrate_to_lowercase` (`reqs.py:53-95`) — that is the
cost, and it is paid only if a fourth holder kind is actually needed.

History is present from day one, not bolted on. `release_reason='terminal:fixed'` is a queryable
record that a commit trailer closed the claim.

**Honest limit on that record:** grouping `release_reason` shows releases that *happened*, not
terminal transitions that *should* have released and did not. Detecting the second — a live claim on
an entity whose status is already terminal — requires cross-referencing claims against entity status,
and **v1 ships no query for it**; that is part of the deferred audit tooling (§10, D4). In v1 the
cross-check is manual: `codebugs claims` then `codebugs get <id>`.

### 5.3 Transaction discipline

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

- **`if conn.in_transaction: yield False`.** Nesting is detected and never attempted. A nested
  `BEGIN IMMEDIATE` raises `SQLITE_ERROR` (code 1), which the contention classifier deliberately
  re-raises (§5.8) — an unguarded nesting bug surfaces as an unhandled exception, not as a silent
  `undetermined`.
- **`if conn.in_transaction` guarding the `ROLLBACK`, plus a swallowed `OperationalError`.** A
  `ROLLBACK` with no active transaction raises `SQLITE_ERROR` whose message contains neither "locked"
  nor "busy". Without this guard the cleanup exception replaces the original and escapes past the
  classifier.
- **`except BaseException`.** A `KeyboardInterrupt` mid-transaction must still roll back.

**Invariant, to be stated in CLAUDE.md and ratcheted by a test:** the codebase contains no plain
`BEGIN`. `db.txn` is the only place `BEGIN IMMEDIATE` is written. Verified: `grep -rn 'BEGIN'
src/codebugs/` returns 6 hits, of which exactly **two are executable** — `merge.py:242` and
`milestones/capacity.py:182` — both pre-existing and **not** refactored by this design (§10, D9). The
ratchet test starts as an allowlist of those two sites plus `db.txn`, and can only shrink.

#### Core / wrapper split

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
        return _undetermined(exc, entity_id=entity_id)   # §5.8
```

**Where the ambient case occurs, traced.** `findings.update_finding` uses Python sqlite3's implicit
transaction management. Its `UPDATE findings …` at `findings.py:298` opens a write transaction and
takes the RESERVED lock; `conn.commit()` follows at `findings.py:299`. The terminal hook fires between
those two lines, so at hook time `conn.in_transaction` is `True` **and the write lock is already
held** — there is no deferred-to-immediate upgrade to lose. `_release_core` emits its statements into
that transaction and returns; `findings.py:299` commits both the status change and the release
atomically, or neither lands. The identical shape holds for `reqs.update_requirement`
(`reqs.py:222` / `:223`).

#### The ambient-transaction invariant — **normative**

> **Every v1 caller of the public layer (`claim`, `release`) MUST hold a connection with no open
> transaction.**

This is not advice; it is the condition under which the public layer is correct. On a connection whose
transaction was opened *implicitly* by an earlier statement, `db.txn` yields `False`, `_claim_core`
writes, **nothing commits**, and `claim` still returns `outcome="claimed"`. This was reproduced
against the real module. It is unreachable in v1 for a specific, checkable reason: `server.py:13-19`'s
`_conn` is a context manager that opens a **fresh** `db.connect()` per tool call and closes it, and the
CLI does the same, so every public-layer caller holds a clean connection. `db.connect()`
(`db.py:492-503`) sets `PRAGMA journal_mode=WAL` at `:497` and never sets `isolation_level`, so
implicit transactions are exactly what a reused connection would produce.

The only ambient path in v1 is the terminal hook, which calls `_release_core` (the **core** layer, not
the public one) and is committed by `findings.py:299` / `reqs.py:223`. **A future ambient consumer
(§10, D1) must own its own commit and must call the core layer through the public ambient API added by
that commit — it must not call `claim` / `release`.** Test 27 asserts the invariant.

**`busy_timeout`, explicitly.** `db.connect()` sets `journal_mode=WAL` and nothing else. The 5000 ms
that turns a losing writer into a clean result rather than an exception is currently *inherited* from
`sqlite3.connect(timeout=5.0)`'s default and appears nowhere in the source. One line, its own
behaviour-neutral commit, landing ahead of everything else:

```python
conn.execute("PRAGMA busy_timeout=5000")   # explicit; was inherited from sqlite3's default
```

`claims.py` does not re-set it. One owner for the setting.

### 5.4 Public API — every signature

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
    holder_kind: str = "agent",
    holder_repo: str | None = None,
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
    holder_kind: str = "agent",
    holder_repo: str | None = None,
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
    busy_status: str | None = None          # NEW, trailing, defaulted — see §5.9

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

**`list_claims` takes no `include_released`, no `divergent_only` and no `stale_after_seconds`, and
there is no `steal`, `history`, `audit`, `git_branch_verifier` or `prune`.** All deferred (§10), none
present under an alias, and none referenced by any code path in this document.

### 5.5 Exact SQL, every path

#### (a) Claim — the guard

Runs inside the same `BEGIN IMMEDIATE`, before the upsert. **The terminal check is unconditional,
gated only on `allow_terminal` — never on projection.**

```python
ref = entities.EntityRef.of(entity_id)      # ValueError on bad format          (entities.py:78)
ref.require(conn)                           # KeyError if absent                (entities.py:105-108)

current = ref.status(conn)                  # ALWAYS read — never gated on projection
if current in ref.kind.terminal and not allow_terminal:
    return _response("entity_terminal", entity_id=entity_id, kind=ref.kind.name,
                     row=_live_claim(conn, entity_id), current_status=current)

busy = ref.kind.busy_status                 # declarative; None == this kind does not project
do_project = project and busy is not None
```

Round 2 wrapped `if project and busy is not None:` around the whole block, which made the terminal
guard unreachable for **every requirement** (`busy_status is None`) and for every `--no-project`
claim. `ref.kind.terminal` is populated for both existing kinds — `FINDING_TERMINAL`
(`types.py:36` = `{"fixed","not_a_bug","wont_fix"}`) and `REQUIREMENT_TERMINAL` (`types.py:41` =
`{"implemented","verified","superseded","obsolete"}`) — so the guard is live for requirements from day
one even though requirements never project.

#### (b) Claim — the upsert

One statement. The `WHERE` compares the **full holder triple** with NULL-safe `IS` on the nullable
column.

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
RETURNING claim_id, entity_id, kind, holder, holder_kind, holder_repo,
          claimed_at, renewed_at, touch_count, note, prev_status, projected_to,
          (touch_count = 1) AS was_new;
```

Executed against SQLite 3.47.1 by the architect and re-executed independently by both final
verifiers:

| Case | `RETURNING` row | Outcome |
|---|---|---|
| no live claim | `touch_count=1, was_new=1` | `claimed` |
| live claim, identical `(holder, holder_kind, holder_repo)` | `touch_count=2, was_new=0` | `already_mine`, `renewed_at` moved |
| live claim, same holder, **different `holder_repo`** | **`None`** | `held_by_other` |
| live claim, same holder+repo, **different `holder_kind`** | **`None`** | `held_by_other` |
| live claim with `holder_repo IS NULL`, repeated with NULL | `touch_count=2` | `already_mine` — `IS` matches NULL to NULL |
| live rows after the whole sequence | — | exactly **1** |

`was_new` is computed **in the row**, never from `cursor.rowcount` — the same idiom as `sweep.py:313`
(`RETURNING (recurrence_count = 1) AS was_new`, `.fetchone()` at `sweep.py:315`).

**Consequence of full-triple identity, stated:** a same-holder retry that supplies *corrected*
metadata (a `holder_repo` that was wrong or missing the first time) is refused as `held_by_other`
rather than repairing the row. That is deliberate — silently overwriting ownership evidence on a live
claim is worse than a refusal that names the incumbent. The repair path is `release` then `claim`, and
the `held_by_other` response carries the incumbent's full triple so the caller can see *why* it looked
like a mismatch.

#### (c) Claim — naming the incumbent, and projecting

**On `held_by_other`** (the upsert returned `None`), one parameterized read in the same transaction:

```sql
SELECT claim_id, entity_id, kind, holder, holder_kind, holder_repo, claimed_at, renewed_at,
       touch_count, note, prev_status, projected_to
  FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;
```

**On `claimed`, and only if `do_project`:**

```python
moved = ref.set_status(conn, new_status=busy, expected=current)     # §5.9, rowcount-based
conn.execute(
    "UPDATE entity_claims SET prev_status = ?, projected_to = ? WHERE claim_id = ?",
    (current, busy if moved else None, claim_id),
)
```

If `moved` is False the status changed between the guard read and the projection — impossible from
another process (the write lock is held from `BEGIN IMMEDIATE`) and, since this design registers no
hook that fires between those two statements, it has no known execution path on the same connection
either. It is checked anyway because the cost is one boolean and the failure mode without the check is
a `prev_status` that release would restore to a value that was never current. `projected_to` stays
NULL, release will not attempt a restore, and the response carries `projected: false,
projection: "raced"`.

**`claim_id` generation** follows `findings._next_id` (`findings.py:117`) in shape:
`SELECT claim_id FROM entity_claims WHERE claim_id LIKE 'CLM-%' ORDER BY CAST(SUBSTR(claim_id, 5) AS
INTEGER) DESC LIMIT 1`, executed **inside** the same `BEGIN IMMEDIATE`, so the read-then-insert is not
a race.

#### (d) Release — authorized on the full NULL-safe holder triple

```sql
UPDATE entity_claims
   SET released_at = ?, released_by = ?, release_reason = ?
 WHERE entity_id   =  ?
   AND holder      =  ?
   AND holder_kind =  ?
   AND holder_repo IS ?
   AND released_at IS NULL
RETURNING claim_id, entity_id, kind, holder, holder_kind, holder_repo,
          claimed_at, renewed_at, touch_count, note, prev_status, projected_to,
          released_at, released_by, release_reason;
```

`cur.fetchone()` — **never `rowcount`** (§5.5(f)).

**Release authorizes on the same triple `claim` compares, with the same NULL-safe `IS`.** Round 3
matched on `holder` alone; that was a real hole, found by the Codex final verifier: a same-text holder
of another `holder_kind` or in another `holder_repo` could release someone else's claim. Ownership is
defined by the triple, so authorization is checked against the triple. The `RETURNING` list is the
**complete row**, which is what lets `_response` (§5.6) populate every common key on the `released`
outcome.

| Result | Outcome |
|---|---|
| row | `released` |
| `None`, and a live claim exists (any holder triple) | `not_yours` — the response names the incumbent's full triple |
| `None`, and no live claim exists | `not_claimed` |

**Disclosed cost of the tightening.** A caller that releases with a triple that does not byte-match
what it claimed with gets `not_yours` and the claim stays live — a leak. Three things bound it:

1. Both shell scripts pass the **same two values** at claim and at release: `--holder "${BRANCH}"`
   (`--holder "${BRANCH_NAME}"` in setup, which is the same string), `--holder-kind branch`, and
   `--repo "${REPO_ROOT}"`. `REPO_ROOT` is defined in both scripts and both run with cwd `REPO_ROOT`
   in normal use.
2. The **primary** release path is the terminal hook, which passes the live row's own triple read
   from the database (§5.10.3) and therefore can never mismatch.
3. The finish-script release failure is non-fatal and prints the exact recovery command including
   `--holder-kind` and `--repo` (§6.3).

**Then, only if `projected_to IS NOT NULL` and `restore_status`:**

```python
restored = ref.set_status(conn, new_status=prev_status, expected=projected_to)
```

which is `UPDATE <table> SET status=?, updated_at=? WHERE id=? AND status=?`. If the holder already
moved the finding to `fixed`, the `status = projected_to` guard fails, `rowcount == 0`, and the status
is left alone. **Release never resurrects finished work.** The response reports
`status_restored: false, current_status: "fixed"`.

#### (e) Read paths — two indexed point queries

```sql
-- who_holds(CB-1234): uses idx_claims_live
SELECT * FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;

-- held_by('fix-cb-2534-…'): uses idx_claims_holder_live. Point query, no fold.
SELECT * FROM entity_claims WHERE holder = ? AND released_at IS NULL ORDER BY claimed_at;

-- list_claims: composable, all filters optional. LIVE CLAIMS ONLY.
SELECT * FROM entity_claims
 WHERE (:kind        IS NULL OR kind        = :kind)
   AND (:holder      IS NULL OR holder      = :holder)
   AND (:holder_kind IS NULL OR holder_kind = :holder_kind)
   AND released_at IS NULL
 ORDER BY renewed_at DESC LIMIT :limit;
```

**All three read paths return live claims only. There is no way to query released rows in v1** — that
is claim history, and history querying is deferred (§10, D5). The rows are retained; only the surface
is absent.

Every returned row is decorated in Python with exactly two derived fields, and no others:

| Field | Meaning |
|---|---|
| `held_seconds` | `now - claimed_at` |
| `idle_seconds` | `now - renewed_at` — the honest staleness signal |

**No `stale`, no `orphaned`, no `divergent`.** Those were audit decorations; audit tooling is deferred
by binding ruling (§10, D4), and a decoration is still the feature. The reader computes staleness from
`idle_seconds` against whatever threshold it chooses.

Response shapes, specified so the implementer does not have to invent them:

```python
who_holds(conn, entity_id=...)  -> <decorated row dict> | None
held_by(conn, holder=...)       -> {"holder": str, "count": int, "claims": [<decorated row>, ...]}
list_claims(conn, ...)          -> {"count": int, "claims": [<decorated row>, ...]}
```

#### (f) The `RETURNING` audit — every statement in this design

| # | Statement | `RETURNING`? | Outcome read from | Safe? |
|---|---|---|---|---|
| 1 | claim upsert, §5.5(b) | yes | `cur.fetchone()`, incl. computed `was_new` | yes |
| 2 | held_by_other lookup, §5.5(c) | no | `fetchone()` on a `SELECT` | yes |
| 3 | `UPDATE entity_claims SET prev_status…`, §5.5(c) | no | not consulted | yes |
| 4 | `EntityRef.set_status`, §5.9 | **no, deliberately** | `cur.rowcount` — valid *because* there is no `RETURNING` | yes |
| 5 | release soft-delete, §5.5(d) | yes | `cur.fetchone()` | yes |
| 6 | all read paths, §5.5(e) | n/a | `fetchall()` | yes |

**Rule for the implementation, to be added to CLAUDE.md:** *a statement either carries `RETURNING` and
its outcome is read by fetching, or it carries no `RETURNING` and its outcome is read from `rowcount`.
Never both.* Executed: for `UPDATE t SET a=2 WHERE a=1 RETURNING a`, `cur.rowcount` is **0 before** the
fetch and **1 after**; the same statement without `RETURNING` gives `1` on a hit and `0` on a miss
immediately. So a `RETURNING` statement whose outcome is read from `rowcount` reports "nothing
happened" **while having performed the write** — strictly worse than a no-op.

### 5.6 Outcome vocabulary, and the one common response builder

```
claim   → claimed | already_mine | held_by_other | entity_terminal | undetermined
release → released | not_yours   | not_claimed   | undetermined
```

There are no other outcomes. `who_holds`, `held_by` and `list_claims` are reads: they return rows
(§5.5(e)), not outcomes.

**Every `claim` / `release` response is built by exactly one function.** Round 3 promised that every
outcome carries every common key while `entity_terminal` and `undetermined` returned partial dicts;
that split is closed by making the builder the only way to construct a response.

```python
_COMMON_KEYS = (
    "outcome", "entity_id", "kind",
    "holder", "holder_kind", "holder_repo",
    "claim_id", "claimed_at", "renewed_at", "touch_count",
    "held_seconds", "idle_seconds",
    "projected", "projected_to", "prev_status",
)

def _response(outcome: str, *, entity_id: str, kind: str | None = None,
              row: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """THE single constructor for every claim/release response.

    Every key in _COMMON_KEYS is present on every outcome, None where not
    applicable. `row` is a full entity_claims row (the live one, or the row just
    written); when it is None the holder/claim fields are None.
    """
    r = dict(row) if row is not None else {}
    now = t.utc_now()
    out: dict[str, Any] = {
        "outcome":      outcome,
        "entity_id":    entity_id,
        "kind":         kind if kind is not None else r.get("kind"),
        "holder":       r.get("holder"),
        "holder_kind":  r.get("holder_kind"),
        "holder_repo":  r.get("holder_repo"),
        "claim_id":     r.get("claim_id"),
        "claimed_at":   r.get("claimed_at"),
        "renewed_at":   r.get("renewed_at"),
        "touch_count":  r.get("touch_count"),
        "held_seconds": _elapsed(r.get("claimed_at"), now),   # None if the input is None
        "idle_seconds": _elapsed(r.get("renewed_at"), now),
        "projected":    r.get("projected_to") is not None,
        "projected_to": r.get("projected_to"),
        "prev_status":  r.get("prev_status"),
    }
    out.update(extra)
    return out
```

**Normative:** `_claim_core`, `_release_core` and `_undetermined` return **only** via `_response`.
There is no other `return {…}` in either module path. Test 28 asserts that all nine outcome paths
(eight distinct strings — `undetermined` occurs on both verbs) carry all fifteen common keys.

`holder`, `holder_kind` and `holder_repo` are always **the live claim's** triple — on `held_by_other`
and `not_yours` that is the *incumbent*, not the caller.

Outcome-specific additions, all passed through `**extra`:

| Outcome | Extra keys |
|---|---|
| `entity_terminal` | `current_status` |
| `undetermined` | `reason` (`"database_busy"`), `retry_after_ms` (`250`), `detail` (the exception string) |
| `released` | `status_restored: bool`, `current_status` |
| `claimed` with a raced projection | `projection: "raced"` |

#### What a caller must do with `undetermined`

**`undetermined` means: the database was too contended to tell you whether the claim was made.** The
claim may or may not exist. **Re-issue the identical call.** That is safe because the primitive is an
idempotent upsert: the same `(entity_id, holder, holder_kind, holder_repo)` quadruple replayed
converges on `already_mine` and can never double-claim. That idempotence is the entire reason the
claim is an upsert rather than a bare `INSERT`.

`retry_after_ms: 250` is a suggestion, not a contract. The shell caller (§6.2) retries exactly once and
then proceeds **unclaimed with a loud warning**, rather than blocking a human's worktree setup on
database contention.

### 5.7 The discriminator is `touch_count`, never a timestamp

`types.utc_now()` (`types.py:12-14`) formats with `"%Y-%m-%dT%H:%M:%SZ"` — **whole seconds**. Two calls
inside the same second produce equal strings, so a `claimed_at == renewed_at` discriminator misreports
a retry as a fresh claim, which is exactly what an agent retrying on a 250 ms loop does. `touch_count`
is a monotone integer incremented by the upsert itself and is clock-independent; `was_new` is computed
from it inside the `RETURNING` clause. Executed: `was_new` came back `1` then `0` on two calls inside
one wall-clock second.

### 5.8 Error handling

Three tiers, matching CLAUDE.md's contract exactly.

| Condition | Behaviour |
|---|---|
| Unparseable entity id | `ValueError` from `EntityRef.of` (`entities.py:78`) — **propagates** |
| Well-formed id, no such row | `KeyError` from `EntityRef.require` (`entities.py:105-108`) — **propagates** |
| SQLite contention (`SQLITE_BUSY` / `SQLITE_LOCKED`) | caught, returned as `outcome="undetermined"` |
| Anything else | **propagates** |

MCP tools let all of it reach FastMCP's built-in error handling. CLI handlers catch
`ValueError`/`KeyError`, print to stderr, `sys.exit(1)`.

The contention classifier keys on **numeric codes, not message strings**:

```python
_CONTENTION = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})    # 5, 6

def _is_contention(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in _CONTENTION          # masks extended codes: 517 & 0xFF == 5

def _undetermined(exc: sqlite3.OperationalError, *, entity_id: str) -> dict[str, Any]:
    if not _is_contention(exc):
        raise exc                                # a real error is NEVER masked as contention
    try:
        kind = entities.EntityRef.of(entity_id).kind.name
    except ValueError:
        kind = None
    return _response("undetermined", entity_id=entity_id, kind=kind, row=None,
                     reason="database_busy", retry_after_ms=250, detail=str(exc))
```

`SQLITE_BUSY_SNAPSHOT` (517) masks to 5 and is classified as contention. *"cannot start a transaction
within a transaction"* and *"cannot rollback - no transaction is active"* are both `SQLITE_ERROR`
(code 1) and are **re-raised** — those are programming errors and must be loud. `sqlite_errorcode`
requires Python 3.11+, which the project already requires. `EntityRef.of` is a pure parse and touches
no database, so it is safe on the error path.

`raise exc` re-raises the original exception object with its traceback intact, from inside the
`except` block that caught it — no wrapping, no chaining noise.

### 5.9 The projection contract, and its preconditions

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

`claims.py` branches on nothing — it reads `ref.kind.busy_status`.

**This is what the user ratified** (`CHECKPOINT-r2.md`): requirements get claim records; claiming a
requirement does **not** change its status; `reqs.py:22-23`'s CHECK constraint is **not** rebuilt.
That CHECK is `CHECK(status IN ('planned','partial','implemented','verified','superseded','obsolete'))`
— `in_progress` is absent, so a projecting requirement would have required the rebuild, and it is not
happening.

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
licenses `entities.py:86`'s `# noqa: S608`. (Note for the implementer: `entities.py:20`'s `_SAFE_IDENT`
regex is **defined and never referenced** — verified, exactly one occurrence in the file. The real
guard on the existing interpolation is the `readable_cols` membership test at `entities.py:83-84`.
Do not add a second unused guard.)

**Invariant — no hook, therefore no recursion.** `set_status` is called only by `claims.py`, only with
`kind.busy_status` (never terminal by construction) or with a `prev_status` that was non-terminal at
claim time (guaranteed by §5.5(a)'s unconditional terminal guard). Therefore no `set_status` call can
ever produce a terminal status, therefore the terminal hook has nothing to react to, and
`claim → project → hook → release → restore → hook → …` is unreachable. Test 19 asserts it.

#### Preconditions on a projecting `EntityKind`

"A third kind is one `EntityKind` entry" is syntactically true and semantically incomplete: it assumes
four things about the target table. They are a **written contract**, and an executed test enforces
three of them.

> **A kind that declares `busy_status` MUST satisfy all four:**
>
> **P1. Schema shape.** Its table has columns `id` (TEXT PK), `status` (TEXT), `updated_at` (TEXT).
> `set_status` writes exactly `status` and `updated_at` and keys on `id`.
>
> **P2. Value admissibility.** The declared `busy_status` value is accepted by the table — it passes
> any CHECK constraint on `status` **and** it is a canonical value, not an alias. `set_status`
> performs **no** resolution: it writes the declared string verbatim, so a kind declaring
> `busy_status="active"` would write `"active"` even though `FINDING_STATUS_ALIASES`
> (`types.py:23`, the `"active": "in_progress"` entry at `types.py:32`) maps it to `"in_progress"`.
> Declare canonical values only.
>
> **P3. `busy_status ∉ kind.terminal`.** Otherwise projection would create a terminal status, which
> breaks the no-recursion invariant above and would make a claim self-releasing.
>
> **P4. Side-effect tolerance.** The kind's domain module accepts that a projected status change does
> **not** run its `update_*` function — no note appended, no `meta` touched, no status-change hook
> fired. The audit trail for a projection lives in `entity_claims`
> (`prev_status`, `projected_to`, `claimed_at`, `release_reason`) instead.

P1–P3 are enforced by test 5(b). **P4 is a review obligation, not a testable one, and it is stated as
such.**

**Bypass cost, stated.** `set_status` writes `status` and `updated_at` and nothing else. A projection
therefore does **not** appear in the finding's notes history: an operator reading `codebugs get
CB-1234` sees the status change with no explanation. Adding a `claim` block to `codebugs get` is
deferred (§10, D7), so **in v1 the explanation lives one command away**, in
`codebugs who-holds CB-1234`. That is a real ergonomic cost and it is not free.

### 5.10 The status-change hook seam

#### 5.10.1 The seam

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
    row. See db.register_status_change_hook's docstring and §5.10.2."""
```

This mirrors `register_post_add_hook` (`db.py:178-190`) / `run_post_add_hooks` (`db.py:193`) exactly:
same registration discipline, same in-transaction contract, same swallow-and-log failure policy. The
create side of that pair already exists; the update side does not. **The asymmetry is the anomaly.**

#### 5.10.2 The firing rule — fire iff the write changed the row

Round 2 fired the hook when "the intended status differs from the pre-read row's status". That is not
the same as "the write happened": a refused write could fire the hook and release ownership on an
entity whose status never became terminal.

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

`status` at that point is already canonicalised by `resolve_finding_status` (`findings.py:260`), so the
comparison is canonical-to-canonical and an alias does not read as a change when the row already holds
the canonical value. The identical shape goes into `reqs.update_requirement` between `reqs.py:222` and
`:223`, with `resolve_requirement_status` (`reqs.py:188`) providing the canonical value. **Both writers
fire it — that is what makes this a seam over the entity layer rather than a findings-specific
callback wearing a general name.**

**Honesty about what `changed` buys today.** `expected_status` is deferred (§10, D2), so today's
`UPDATE` has no CAS predicate and `cur.rowcount` is 0 only if the row vanished between
`findings.py:252` and `:298`. The guard is therefore **defensive in v1 and load-bearing the moment
`expected_status` lands**, at which point the `WHERE` gains `AND status = ?` and `rowcount == 0`
becomes the ordinary refusal signal. Writing it now means the refusal path is already correct when the
CAS arrives. **It is not claimed to prevent an observable bug today.**

#### 5.10.3 The hook `claims.py` registers

```python
def _auto_release_on_terminal(conn, entity_id, old_status, new_status) -> None:
    ref = entities.EntityRef.of(entity_id)              # ValueError → swallowed by the runner
    if new_status not in ref.kind.terminal:
        return
    live = _live_claim(conn, entity_id)
    if live is None:
        return
    _release_core(conn, entity_id=entity_id,
                  holder=live["holder"],                # the live row's OWN triple —
                  holder_kind=live["holder_kind"],      # authorization can never mismatch
                  holder_repo=live["holder_repo"],
                  restore_status=False,                 # the entity is FINISHED; never restore
                  reason=f"terminal:{new_status}", released_by="hook:status_change")

db.register_status_change_hook("claims_auto_release", _auto_release_on_terminal)
```

`restore_status=False` is load-bearing: a terminal status is the *point*, and restoring `prev_status`
would undo the finding's resolution.

`_release_core`, not `release` — the hook runs inside `update_finding`'s open transaction (§5.3), so it
must emit statements and not manage a transaction. This is the sole reason the core layer exists now
that `pull_next` integration is deferred.

#### 5.10.4 The end-to-end sequence this exists for

An agent claims CB-1234 → `entity_claims` row live, `findings.status='in_progress'`,
`prev_status='open'`, `projected_to='in_progress'`. The agent commits `Fixes: CB-1234`. On integration,
`worktree-finish.sh:1249-1257` runs `auto-resolve-codebugs.py`, which calls
`codebugs update CB-1234 --status fixed` → `update_finding` → `changed=True` →
`run_status_change_hooks(conn, 'CB-1234', 'in_progress', 'fixed')` → the claim is soft-closed with
`release_reason='terminal:fixed'`, **inside the same transaction as the status write**, committed
together at `findings.py:299`.

Final state: CB-1234 `fixed`, unclaimed, and the record says a commit trailer closed it.

**What this does NOT do:** it does not make the provenance path claim-aware, and it does not stop a
commit from resolving a finding held by someone else. That is correct — a commit that fixes a bug
fixes it regardless of who held the card.

#### 5.10.5 Disclosed cost, and the fallback if reviewers reject the seam

Hook exceptions are swallowed, copying `run_post_add_hooks`' policy deliberately: a failed
auto-release must never break a status write. The degraded state is **a live claim on an entity whose
status is already terminal**. In v1 that state is discoverable but not *queryable* — the automated
divergence report is part of the deferred audit tooling (§10, D4), so finding it means listing claims
and checking the entities' statuses by hand. The belt is `worktree-finish.sh`'s unconditional release
(§6.3), which closes exactly this case for the branch workflow.

**If the seam is rejected in review, the design degrades to that manual cross-check plus the finish
release: recoverable, but not automatic.** It costs the automatic clearer, not the design. Swallowing
exceptions means "the clearers keep the table trustworthy" is an aspiration backed by a recoverable
failure mode, not a guarantee.

### 5.11 CLI surface

`claims.py` provides `register_cli(sub, commands)` and calls
`db.register_cli_provider("claims", register_cli)` at module level, per CLAUDE.md.

```
codebugs claim <ID>     --holder H [--holder-kind branch|agent|human] [--repo PATH]
                        [--note TEXT] [--no-project] [--allow-terminal] [--json]
codebugs release <ID>   --holder H [--holder-kind branch|agent|human] [--repo PATH]
                        [--no-restore] [--reason TEXT] [--json]
codebugs who-holds <ID> [--json]
codebugs claims         [--holder H] [--kind K] [--holder-kind K]
                        [--format ids|table] [--json]
```

`release` takes `--holder-kind` and `--repo` because release authorizes on the full triple (§5.5(d)).
Their defaults match `claim`'s: `--holder-kind agent`, `--repo` absent → `holder_repo = NULL`.

`--format ids` on `codebugs claims` prints **one bare entity id per line and nothing else** — no
header, no decoration, empty output when there are no matches. This is what lets `worktree-finish.sh`
(§6.3) drive a loop without grep-parsing JSON. Exit status is `0` on an empty result set: "you hold
nothing" is a successful answer, not an error.

`--format table` (the default for `claims`) uses `fmt.format_table`. `--json` emits the full response
dict on every verb.

**Human stdout lines, specified** (one stable line per verb, so shell messages can refer to them):

```
claim      claimed CB-1234 as fix-cb-1234-x (branch, /home/faxik/w/autosorter) touch=1
claim      already yours: CB-1234 held by fix-cb-1234-x (branch, /home/faxik/w/autosorter) touch=4
claim      REFUSED CB-1234: held by fix-cb-1234-other (branch, /home/faxik/w/autosorter) since 2026-08-06T09:12:31Z
claim      REFUSED CB-1234: already fixed (use --allow-terminal to claim anyway)
claim      UNDETERMINED CB-1234: database busy, retry in 250ms
release    released CB-1234 (was in_progress, restored to open)
release    nothing to release for CB-1234
release    REFUSED CB-1234: held by fix-cb-1234-other (branch, /home/faxik/w/autosorter)
who-holds  CB-1234 held by fix-cb-1234-x (branch, /home/faxik/w/autosorter) since … idle 41s
who-holds  CB-1234 not held
```

#### Exit codes are the API for shell callers

This is what makes the surface usable from a `set -euo pipefail` script without parsing.

| code | `claim` | `release` | `who-holds` | `claims` |
|---|---|---|---|---|
| 0 | `claimed` or `already_mine` → proceed | `released` or `not_claimed` → done either way | held (prints the holder line) | success, including an empty list |
| 1 | error: bad id format, no such entity, DB not found | same | same | same |
| 3 | `held_by_other` → refuse | `not_yours` | **not held** | — |
| 4 | `entity_terminal` → refuse | — | — | — |
| 5 | `undetermined` → retry | `undetermined` | — | — |

**Exit code 5 must actually be emitted by both `claim` and `release`.** §6.2's retry branches on it; if
the CLI never emits 5 a contended database falls silently into the catch-all branch and the setup
proceeds unclaimed while believing it retried. Test 25 asserts it by `subprocess`, and it is a deploy
prerequisite (§9).

**`who-holds` returns 0/3 only.** Two states: someone holds it, or nobody does.

**`release` returns 0 on `not_claimed`** deliberately. The finish script calls release
unconditionally; "there was nothing to release" is the expected, successful, common case.

#### What the CLI does not have

No `steal`, no `claim-history`, no `claims-audit`, no `--prune`, no `--before`, no `--all`, no
`--divergent`, no `--stale-after`. Round 2 promised `claims prune --before <iso>` in prose and put it
in no CLI surface; that promise is **withdrawn**, not silently kept.

**Retention: none in v1, and no verb for it.** A closed claim row is ~200 bytes and the realistic rate
is tens per week. If it ever matters, retention is a `DELETE FROM entity_claims WHERE released_at < ?`
— a query, not a migration — added with full knowledge of what the table actually accumulated
(§10, D6).

### 5.12 MCP tools

`claims.py` defines `register_tools(mcp, conn_factory)` and calls
`db.register_tool_provider("claims", register_tools)` at module level. All five return
`dict[str, Any]` and let exceptions propagate to FastMCP, per CLAUDE.md.

```python
claims_claim(entity_id: str, holder: str, holder_kind: str = "agent",
             holder_repo: str | None = None, note: str = "",
             project: bool = True, allow_terminal: bool = False) -> dict[str, Any]
claims_release(entity_id: str, holder: str, holder_kind: str = "agent",
               holder_repo: str | None = None, restore_status: bool = True,
               reason: str = "explicit") -> dict[str, Any]
claims_who_holds(entity_id: str) -> dict[str, Any]
claims_held_by(holder: str) -> dict[str, Any]
claims_list(kind: str | None = None, holder: str | None = None,
            holder_kind: str | None = None, limit: int = 200) -> dict[str, Any]
```

Domain-prefixed per CLAUDE.md's naming rule — this is a new module with no external consumers naming
its tools, so neither the findings nor the milestones exception applies.

`claims_who_holds` returns `{"held": false, "entity_id": …, "claim": null}` rather than `None` when
nothing holds it, because an MCP tool must return a dict.

**No git subprocess is spawned from any MCP request.** This design makes **no git subprocess call from
Python at all**; the tool that would have (a branch verifier launching up to 2N sequential git
processes) is deferred (§10, D4).

### 5.13 Migration and back-compat

**Schema.** `entity_claims` is a new table created by `CREATE TABLE IF NOT EXISTS` inside
`claims.ensure_schema`, registered via `db.register_schema("claims", ensure_schema)`. Additive by
construction. **No existing table is altered** — not `findings`, not `requirements`, not
`milestone_items`, and specifically **not `reqs.py:22-23`'s CHECK constraint**.

**`EntityKind.busy_status`** is a trailing field with a `None` default on a frozen dataclass, so every
existing `EntityKind(...)` construction and every unpacking of `ENTITY_KINDS` keeps working unchanged.
`tests/test_entities.py` needs no edit.

**API back-compat.**

- `update_finding` / `update_requirement`: **no signature change.** The only difference is an
  in-function hook call. Response dicts are unchanged; the `"changed"` response key is **not** added
  (§10, D2).
- No MCP tool signature changes. No CLI flag changes to existing verbs.
- **No behaviour change to `pull_next`.** Its integration is deferred, so `pull_next` is byte-identical
  to today, and `tests/test_milestones.py:801-846` must pass unmodified.

**Wiring, per CLAUDE.md's "Current rules for new code":**

| File | Change |
|---|---|
| `db.py:487` | add `claims` to `from codebugs import findings, provenance, reqs, merge, sweep, bench, blockers, milestones  # noqa: F401` in `_ensure_modules_loaded()`. **This import is what makes `register_status_change_hook` run at all** — without it the hook never registers and the terminal clearer silently does not exist. |
| `server.py:22-32` | `SERVER_NAMES["claims"] = "codeclaims"` |
| `cli.py:49` | add `"claims"` to the `--mode` `choices` list (currently `["findings", "provenance", "reqs", "merge", "sweep", "bench", "blockers", "milestones", "all"]`) |

**No data migration. No backfill of the ~41 existing `in_progress` findings.** The table starts empty
*on purpose*. Backfilling would import exactly the garbage that makes the current signal ungateable,
with invented holders and invented claim times. Those 41 remain a `findings.status` problem, visible
via `codebugs query --status in_progress`, and they are someone's cleanup task — not this table's
contents.

**Consequence, stated plainly:** on day one, `who-holds` returns "not held" for every card in flight.
The gate is therefore permissive at launch and becomes protective as claims accumulate — but the
*atomic* property is available from the first concurrent pair, with no warm-up.

**Rollback.** `DROP TABLE entity_claims` plus reverting the code leaves `findings` and `requirements`
exactly as they were, because the only thing claims ever wrote into them is a status value they were
always allowed to hold.

**CLAUDE.md amendments this design requires** (each is otherwise undiscoverable):

1. `entities.py` is the sanctioned cross-table status **write** as well as read; `EntityRef.set_status`
   is the only such write.
2. The `RETURNING` rule: a statement reads its outcome by fetching **or** from `rowcount`, never both.
3. Never write a plain `BEGIN`; `db.txn` is the only place `BEGIN IMMEDIATE` should appear. Known
   pre-existing exceptions: `merge.py:242`, `milestones/capacity.py:182`.
4. A `Claims module` section alongside the existing `Milestones module` section, stating the
   ambient-transaction invariant (§5.3).
5. `register_status_change_hook` documented beside `register_post_add_hook` in the
   debt/extension-point bullet.

---

## 6. Shell adoption — `/home/faxik/w/autosorter/tools/`

Every anchor below was opened this run against the absolute paths. `worktree-setup.sh` is **274
lines**; `worktree-finish.sh` is **1377 lines**. Neither contains a `trap`; neither contains `flock`
except `worktree-finish.sh`'s existing integration lock (`:1241`).

### 6.0 The structural rule these diffs obey

> **Exactly ONE new `codebugs` call may be fatal: the claim gate, placed BEFORE `git worktree add`
> (`worktree-setup.sh:143`). Every other new call is `if`-guarded or `|| true`.**

That matches every `codebugs` call already shipped in these scripts — `worktree-setup.sh:111`
(`if command -v … ; then`), the `if codebugs update …` at `:209`, the same shape in the `--items`
block, and `worktree-finish.sh:1249` / `:1262` / `:1288` / `:1322` (all
`[[ "${SKIP_CHECKS}" != true ]] && [[ -x … ]]` with a `|| echo "⚠ … (non-fatal)"` tail).

The rule makes the worst possible defect — killing `worktree-finish.sh` **after** `merge --no-ff`
(`:1198`) has landed on main — impossible by construction rather than by care.

**Precondition on both scripts (verify before implementing).** The claim written by
`worktree-setup.sh` and the release issued by `worktree-finish.sh` must reach the **same** tracker.
`db.connect()` walks up from cwd for `.codebugs/` and follows a `.git` *file*'s `gitdir:`/`commondir`
pointer, so a call from inside a linked worktree resolves to the main repo's DB. Both scripts run with
cwd = `REPO_ROOT` in normal use (`worktree-finish.sh:1317` states this explicitly for its own block).
Shell check S-c asserts the round trip.

---

### 6.1 Commit **S0** — ancestry filter. Independent of the ledger. Land it first, or alone.

**This closes the merged-but-undeleted false-positive class with no new state, and it is what Round 2
wrongly claimed only a claim ledger could do.** It has no dependency on `codebugs` at all.

**Insert into `worktree-setup.sh` between `:88` and `:90`** (`:88` closes the `others=` pipeline; `:89`
is blank; `:90` is `    if [[ -n "${others}" ]]; then`):

```bash
    # Integration is `merge --no-ff` (worktree-finish.sh:1198) and nothing ever
    # deletes a branch — `git branch -d/-D/--delete` has ZERO occurrences in that
    # script, which removes the worktree (:1338) and leaves the ref forever. So
    # every INTEGRATED branch is still listed by `git branch`, and refusing on it
    # is a pure false positive — the one that trains people to pass
    # --allow-duplicate by reflex. `--merged` is exact here *because* integration
    # is --no-ff: an integrated branch is a strict ancestor of the base.
    if [[ -n "${others}" ]]; then
        _merged=$(git -C "${REPO_ROOT}" branch --merged "${BASE_BRANCH}" \
            --format='%(refname:short)' 2>/dev/null || true)
        if [[ -n "${_merged}" ]]; then
            others=$(printf '%s\n' "${others}" \
                | grep -vxF -f <(printf '%s\n' "${_merged}") || true)
        fi
    fi
```

`BASE_BRANCH` is in scope — defined at `:39` (`BASE_BRANCH="${2:-HEAD}"`), well before `:86`. Probed:
`grep -vxF -f <(…) || true` survives `set -euo pipefail` on both a partial match (one line remains) and
a total match (empty output, script survives). **The `|| true` is on the pipeline, not inside a loop
body** — that placement error is what made the Round-2 finish block fatal.

**Effect:** `others` retains only branches that are *not* yet merged, so `:90-105`'s refusal fires only
on genuine work in flight. **+12 lines, no deletions, no new dependencies, no `codebugs` call.**

---

### 6.2 Commit **S1** — the claim gate in `worktree-setup.sh`

#### (a) Helpers + abort trap — insert immediately after `:81` (`_claim_ids=""`), before `:82` (`for cb in ${CB_IDS}; do`)

```bash
# --- entity-claim gate (codebugs) -------------------------------------------
_claim_one() {                      # returns the CLI's exit code verbatim
    codebugs claim "$1" --holder "${BRANCH_NAME}" --holder-kind branch \
        --repo "${REPO_ROOT}" --note "worktree-setup ${SLUG}"
}

# THE GATE. Called from both claim loops (branch-derived ids and --items ids).
# This is the ONLY new codebugs call in either script permitted to be fatal: it
# runs before `git worktree add` (:143), so a refusal costs a re-run and nothing
# irreversible has happened.
_claim_gate() {
    local cb="$1" _rc=0
    if command -v codebugs >/dev/null 2>&1 && [[ -z "${AUTOSORTER_SETUP_NO_CLAIM:-}" ]]; then
        # `|| _rc=$?` suppresses `set -e` for this command and captures the code.
        # An UNGUARDED call here would kill the script before `case` ever ran.
        _rc=0; _claim_one "${cb}" || _rc=$?
        if [[ "${_rc}" == "5" ]]; then          # undetermined: DB contended
            sleep 1
            _rc=0; _claim_one "${cb}" || _rc=$?  # re-test the CODE, not a boolean
        fi
        case "${_rc}" in
            0)  _claim_ids="${_claim_ids} ${cb}" ;;
            3)  echo ""
                echo "  ${cb} is claimed by another holder (see the REFUSED line above)."
                echo "  CB-2431 and CB-2534 were each implemented twice. Both of those"
                echo "  were SEQUENTIAL launches, which the git guard above already"
                echo "  refuses; this gate additionally refuses the CONCURRENT case,"
                echo "  which that guard cannot see because the two branch names"
                echo "  differ by construction."
                echo ""
                echo "  Inspect:        codebugs who-holds ${cb}"
                echo "  If it is dead:  codebugs release ${cb} --holder <that-holder> \\"
                echo "                      --holder-kind <that-kind> --repo <that-repo>"
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
    return 0
}

# Give back any claim taken below if setup dies before the worktree exists.
#
# EXIT, not ERR: an explicit `exit` does NOT fire an ERR trap — probed
# (`trap "…" ERR; exit 1` printed nothing; the same script with EXIT printed).
# Installed BEFORE the loop so an `exit 1` on the SECOND card still releases the
# FIRST card's claim.
_release_claims_on_abort() {
    local cb
    for cb in ${_claim_ids}; do
        codebugs release "${cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
            --repo "${REPO_ROOT}" --reason "worktree-setup aborted" >/dev/null 2>&1 || true
    done
}
trap _release_claims_on_abort EXIT
```

**Every variable this block reads is defined earlier in the file, verified by opening each line:** `REPO_ROOT` at `:14`, `ITEMS` at `:17`, `ALLOW_DUPLICATE=0` at `:18` (parsed at `:23`), `BRANCH_NAME="$1"` at `:38`, `SLUG` at `:42`, and `_claim_ids=""` at `:81` — all before the insertion point. `local cb`
shadows the enclosing loop's `cb`. `|| true` on the release keeps a failing trap from altering the
script's exit status. `for cb in ${_claim_ids}` over an empty string is zero iterations under `set -u`
— probed. **The release passes the same `--holder-kind branch --repo "${REPO_ROOT}"` the claim used**,
which is what release now authorizes on (§5.5(d)).

`return 0` on `_claim_gate` is load-bearing: without it the function's status is the last command's,
and a bare call in a loop under `set -e` could abort on an arm that legitimately ends non-zero.

#### (b) Replace `:107-135` — the whole `# Registry check + claim` block, comment through closing `fi`

```bash
    # See _claim_gate above. This also REPLACES the old `codebugs get | sed`
    # pre-read: "Only `open` cards are flipped … a follow-up branch on a `fixed`
    # card must not silently reopen it" (the rule stated at :205-207) is now the
    # claim's `entity_terminal` outcome, decided inside one transaction instead of
    # by a shell check-then-act.
    #
    # AUTOSORTER_SETUP_NO_CLAIM (honoured inside _claim_gate) lets tests exercise
    # the pure-git guard above without writing to the real findings database.
    _claim_gate "${cb}"
```

This sits inside the `for cb` loop that closes at `:136`, therefore before `mkdir` at `:139` and before
`git worktree add` at `:143`.

**Outcomes 3, 4 and 5 are handled distinctly.** Round 2 collapsed them into one boolean retry, so a
rival winning during the `sleep` produced a loser that proceeded. The retry re-reads the exit code and
a still-contended second attempt lands in `5`, not in `0`.

**`_claim_ids` is only appended on rc 0.** With `--allow-duplicate` past a `3` or `4`, the script
proceeds **without** holding the claim, so the abort trap has nothing to give back that it never took.

#### (c) `--items=CB-N` ids join the pre-worktree claim set — insert between `:136` (`done`) and `:139` (`mkdir -p`)

`--items` is parsed at `:22` into `ITEMS` and consumed by the milestone-marking block that begins at
`:220` (its comment header starts at `:216`) and closes at `:233` — **after** `git worktree add`. That
block **stays exactly where it is**; only the claim gating moves earlier.

```bash
# --items ids are claimed HERE, before `git worktree add` (:143) — a claim taken
# after the worktree exists gates nothing. The milestone-marking block at
# :216-233 is unchanged and still runs after the worktree is created.
#
# Normalized to the same `CB-<n>` / `FR-<n>` / `NFR-<n>` shape the branch-derived
# ids use, uppercased, and de-duplicated against CB_IDS so a card named in BOTH
# the branch and --items is claimed once (a second claim would be `already_mine`,
# but the id would then appear twice in _claim_ids and be released twice).
# Tokens that are not entity ids are skipped: --items also carries milestone item
# refs, which have no claimable entity.
_item_ids=""
for _it in ${ITEMS//,/ }; do
    _pfx=$(printf '%s' "${_it}" | grep -oiE '^(nfr|fr|cb)' | tr '[:lower:]' '[:upper:]' || true)
    _num=$(printf '%s' "${_it}" | grep -oE '[0-9]+$' || true)
    [[ -n "${_pfx}" && -n "${_num}" ]] || continue
    _norm="${_pfx}-${_num}"
    case " ${CB_IDS} ${_item_ids} " in *" ${_norm} "*) continue ;; esac
    _item_ids="${_item_ids} ${_norm}"
done

for cb in ${_item_ids}; do
    _claim_gate "${cb}"
done
```

`ITEMS` is initialised to `""` at `:17`, so `${ITEMS//,/ }` is safe under `set -u` and the loop runs
zero times when `--items` is absent. Both `grep` calls carry `|| true` on the substitution, so a
non-matching token cannot kill the script under `pipefail`. A refused `--items` claim aborts on the
same rc 3/4 path as a branch-derived one, still **before** `mkdir` at `:139`.

#### (d) Disarm the trap — insert immediately after `:143` (`git … worktree add -b …`)

```bash
# The branch now exists and carries the claim; worktree-finish.sh gives it back.
# Disarm HERE, not at the end of the script: a later symlink/testmon failure must
# NOT hand the card back while a real worktree is sitting on disk. The residual
# is stated in §6.4.
trap - EXIT
```

Probed: `trap - EXIT` disarms cleanly and nothing fires on the subsequent exit.

#### (e) Delete `:197-215` — the write-only projection loop and its comment block

`:197-207` is the `# Auto-claim the card now that the worktree exists (CB-2489 fix (c)).` comment
block, whose `:205-207` states the "only `open` cards are flipped" rule now encoded in
`entity_terminal`. `:208-214` is the complete `for cb in ${_claim_ids}; do … codebugs update "${cb}"
--status in_progress … done`. `:215` is the blank line before `:216`'s `# Optional: flag codebugs
items…` comment. **Deleting `197-215` inclusive leaves `:195` (`fi`), `:196` (blank), then the
`--items` comment at `:216` — a syntactically complete file with no orphaned `fi`/`done`.** Verified by
splicing the deletion into the real file and running `bash -n`.

The loop is redundant because the gate already projected `findings.status='in_progress'` through
`EntityKind.busy_status` (§5.9), inside the same transaction as the claim. This deletes the last
write-only status write in the script.

**One disclosed behaviour change:** today `AUTOSORTER_SETUP_NO_CLAIM` guards `:111` but **not**
`:208-214`, so the env var suppresses the *read* and still performs the `in_progress` *write*. After
(e), `_claim_ids` is populated only inside the guarded gate, so the variable finally means what its
name says: no writes to the findings DB at all.

**Net for S1: roughly +75 / −48 lines.** One check-then-act shell race removed (the `get`-then-`update`
read-then-write). One new obligation added (the abort trap).

---

### 6.3 Commit **S2** — unconditional release in `worktree-finish.sh`

**Insert between `:1333` (the `fi` closing the `[7e/9]` rollup block) and `:1334` (blank, before
`:1335`'s `# 8. Clean up worktree`).**

```bash

# [7f/9] Release entity claims held by this branch.
#
# Deliberately NOT gated on ${SKIP_CHECKS} (:578/:584): releasing a claim is
# lifecycle correctness, not a quality check. A successful `--skip-checks` finish
# that left the claim live would leak ownership BY CONSTRUCTION, which is the
# exact pathology this mechanism exists to remove.
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
    # `|| true` is on the command substitution, NOT inside a loop body.
    _held=$(codebugs claims --holder "${BRANCH}" --format ids 2>/dev/null || true)
    if [[ -z "${_held}" ]]; then
        echo "  ✓ nothing held (released by the terminal hook, or never claimed)"
    fi
    for _cb in ${_held}; do
        # Release authorizes on the full holder triple (holder, holder_kind,
        # holder_repo), so this MUST pass the same --holder-kind/--repo that
        # worktree-setup.sh claimed with. A mismatch returns `not_yours` (rc 3)
        # and the claim stays live — hence the recovery line below.
        if codebugs release "${_cb}" --holder "${BRANCH}" --holder-kind branch \
               --repo "${REPO_ROOT}" --no-restore \
               --reason "branch merged" >/dev/null 2>&1; then
            echo "  ✓ ${_cb} released"
        else
            echo "  ⚠ ${_cb}: release failed (non-fatal). Inspect and run by hand:"
            echo "      codebugs who-holds ${_cb}"
            echo "      codebugs release ${_cb} --holder ${BRANCH} --holder-kind branch \\"
            echo "          --repo ${REPO_ROOT} --no-restore"
        fi
    done
fi
```

**Ordering:** this sits after `[7b/9]`'s `auto-resolve-codebugs.py` (`:1249-1257`), which flips findings
to `fixed` and thereby fires the terminal hook (§5.10.4) and auto-releases the claim already. By the
time `[7f/9]` runs it is **usually a no-op — and that is the point**: it is the belt for the cases the
trailer did not cover (a branch merged with no `Fixes:` trailer, a card worked but not resolved).
Round 2 said the same thing and then made the no-op path the fatal one; this version prints a line and
exits the block.

**`--no-restore`, deliberately.** The branch merged: work landed. Flipping the card back to `open` would
erase that signal. If `[7b/9]` already moved it to `fixed`, §5.5(d)'s `expected = projected_to` guard
would refuse the restore anyway — `--no-restore` makes the intent explicit rather than relying on the
guard.

**Step label `[7f/9]`:** `[7b]`…`[7e]` are taken (`:1251`, `:1264`, `:1290`, `:1324`) and the
`/8`-vs-`/9` denominators are already inconsistent in the shipped file (`:658` prints `[1/8]`).
`[7f/9]` follows the local convention of the block it joins.

**Roughly +38 lines, no deletions.**

---

### 6.4 What the adoption does NOT cover — stated, not hidden

**Residual leak after the disarm point (§6.2(d)).** If setup fails *between* `git worktree add`
(`:143`) and the end of the script — a symlink failure, a testmon copy failure — the trap is already
disarmed, so the claim stays. This is deliberate: the alternative (a trap armed to the end) releases
ownership while a real worktree sits on disk. The residual is visible (`codebugs who-holds <id>` names
the branch) and recoverable (`codebugs release <id> --holder <branch> --holder-kind branch --repo
<repo>`), and the branch genuinely exists, so the claim is not lying about anything.

**Release-authorization mismatch.** If the two scripts ever disagree about `REPO_ROOT`'s exact string,
the finish release returns `not_yours` and the claim leaks until released by hand. Bounded by
§5.5(d)'s three mitigations; the recovery command is printed at the point of failure.

**Not covered at all:** `fix-latest-codebugs/SKILL.md` and any other prompt-level instruction. Those
are advisory, outside the repo, and **do not count as adoption.**

---

## 7. Known Risks and Mitigations

These are the findings that survived three rounds and two independent final verifications. They are
not resolved; they are bounded.

| # | Risk | Why it survives | Mitigation |
|---|---|---|---|
| 1 | **The gate may never be reached.** With `pull_next` integration deferred, the delivery has **exactly one consumer, in another repository's shell script.** If `worktree-setup.sh` is not the path being used for a piece of work, the claim is never taken and the record is advisory again. | This is the "correct primitive dying unwired" precedent the problem brief exists to warn about, and deferring `pull_next` makes it worse, not better. | Named as the **single largest delivery risk**. §8 Q2 escalates it as a live product decision rather than burying it. `release_reason` counts and `codebugs claims` give a cheap adoption check after two weeks. |
| 2 | **The prevented subclass has never been observed.** The window is real in shipped code; its incidence is unmeasured. | No one in the council established that either recorded incident was concurrent (§2.1). | §9's validation measurement records whether the window is reachable. It is a **measurement, not a gate** — the build decision is settled by the user. |
| 3 | **Tightened release authorization can leak a claim.** A release whose holder triple does not byte-match the claim's returns `not_yours` and the claim stays live. | It is the direct cost of closing the hole where a same-text holder of another kind or repo could release someone else's claim. | Both scripts pass identical `--holder-kind` / `--repo` (§6.2, §6.3); the primary release path (the terminal hook) uses the live row's own triple and cannot mismatch; the finish script prints the exact recovery command. Test 29 covers both directions. |
| 4 | **Hook failures are swallowed**, leaving a live claim on a terminal entity. | Copying `run_post_add_hooks`' policy is deliberate: a failed auto-release must never break a status write. | The state is recoverable but **not queryable in v1** — the divergence report is deferred with the audit tooling (§10, D4). The belt is the unconditional finish release (§6.3). Stated as an aspiration backed by a recoverable failure mode, not a guarantee. |
| 5 | **Residual leak after the trap disarm** (§6.4). | The alternative — a trap armed to the end of the script — releases ownership while a real worktree sits on disk, which is worse. | Visible via `who-holds`, recoverable by one command, and the branch genuinely exists so the claim is not false. |
| 6 | **Shell is the highest-risk artifact.** Another repository, `set -euo pipefail`, and the one fatal call lives there. Every Round-2 FATAL was in shell. | Bash under `set -euo pipefail` punishes exactly the idioms this integration needs (`grep` no-match, `$?` after an unguarded call, `ERR` vs `EXIT`). | §6.0's one-fatal-call rule; every construct probed under `set -euo pipefail`; both patched scripts must pass `bash -n` (§9 gate 3); the abort-before-`worktree add` behaviour is a deploy gate (§9 gate 4), not a test. |
| 7 | **`--items` claiming adds a new refusal surface.** A `--items` id held by someone else now aborts setup, where before it did nothing. | It is the point of the change — a claim taken after the worktree exists gates nothing. | The refusal happens before `mkdir` (`:139`), so nothing irreversible has occurred; `--allow-duplicate` is the documented escape; non-entity `--items` tokens (milestone item refs) are skipped by construction. |
| 8 | **Day-one permissiveness.** The table starts empty, so `who-holds` answers "not held" for every card in flight. | Backfilling would import the same garbage that makes `in_progress` ungateable. | Stated plainly (§5.13). The *atomic* property needs no history and works from the first concurrent pair. |
| 9 | **`entities.py` becomes a writer** and gains a second interpolated identifier. | Any alternative breaks "adding a kind is a single entry in `ENTITY_KINDS`". | One new interpolation site inside the module that already owns the only other one, guarded against the frozen tuple, never against caller input; CLAUDE.md amendment 1 makes the exception explicit rather than undocumented. |
| 10 | **`findings.py` / `reqs.py` hot-path edit.** The hook call goes into the two most-used write functions in the repo. | There is no way to make terminal auto-release atomic without it. | ~8 lines each, no signature change, guarded by `changed`; hook failures are swallowed so a hook can never break a status write; test 18 asserts all three arms of the guard. |
| 11 | **No staleness *detection*, only staleness *reporting*.** Nothing distinguishes a dead agent from a slow one. | The git-liveness predicate is dead (§4.2) and its ancestry-based replacement is deferred with the verifier. | v1 offers `idle_seconds` on every row and a manual `release`. That is "visible and recoverable"; it is **not** "detectable", and this is the success criterion the delivery meets least well. |
| 12 | **The ~41 stale `in_progress` findings are untouched.** | No backfill, on purpose. | They remain a `findings.status` cleanup task, visible via `codebugs query --status in_progress`. |

---

## 8. Open Questions

Three, and only one of them blocks anything.

**Q1 — Is the concurrent window reachable in this workflow?** Two `worktree-setup.sh` invocations for
one card with different slugs, launched concurrently: do both create worktrees? Nobody knows. §9's
validation measurement answers it. **It does not gate the build** — the user has settled that decision
(§4.1) — and it does not re-open the design. It calibrates how loudly the mechanism may be described,
and it is worth ten minutes.

**Q2 — Should `pull_next` integration (§10, D1) move back into v1?** Deferring it removed four
independently-found defects at once, including pseudocode that could not run. It also leaves the
delivery with exactly one consumer, in another repository (risk 1). **This is a product decision, not
an architectural one**, and it is worth making explicitly rather than inheriting from a scope ruling.
If the answer is yes, D1 returns with a full correctness pass attached.

**Q3 — Should `worktree-finish.sh` start deleting merged branches (§10, D12)?** Branches accumulate
forever today and each one blocks every future branch for its card through the `:86-88` guard. Doing
D12 would make S0's ancestry filter mostly redundant (a deleted branch is not in `others` at all).
**Do S0 first regardless** — it is safe, reversible, and needs no decision about whether deleting
branches is acceptable in this workflow.

---

## 9. Deploy Prerequisites

Hard, falsifiable gates. Each either passes or it does not, and each names the command that decides.
**"The tests pass" is NOT a gate** — a green suite proves nothing about the six properties this
delivery actually stands on.

| # | Gate | Falsified by |
|---|---|---|
| **G1** | **Two connections, one winner.** Test 1 runs two threads, each opening its own `db.connect(tmp_project)` (the production discovery path), synchronised on `threading.Barrier(2)`, both claiming the same entity. **Exactly one returns `claimed`; the other returns `held_by_other` and its response names the winner's full holder triple.** | Two `claimed`, or two live rows, or a loser whose response does not name the winner. |
| **G2** | **The CLI emits exit code 5.** `codebugs claim <id> …` against a database whose write lock is held by another connection with `busy_timeout=0` exits **5**, via `subprocess` with a real process boundary. Same for `codebugs release`. | Any other exit code. If 5 is never emitted, §6.2(a)'s retry is dead code and a contended database silently proceeds unclaimed. |
| **G3** | **Both patched shell scripts pass `bash -n`.** Apply §6.1, §6.2(a)–(e) and §6.3 to real copies of the 274-line `worktree-setup.sh` and the 1377-line `worktree-finish.sh`, bottom-up so line numbers stay valid, then `bash -n` each. | A syntax error, an orphaned `fi`/`done` from the `:197-215` deletion, or an anchor that does not match the real file. |
| **G4** | **The gate aborts before `git worktree add`.** Shell check S-b: run the patched `worktree-setup.sh` for a two-card branch name where the **second** card is held by another holder. Assert rc 1, **no worktree on disk, no branch created**, and that the **first** card's claim was released by the EXIT trap. | A worktree or branch existing after the refusal, or the first card still held. |
| **G5** | **One tracker, both ends.** Shell check S-c: setup creates the claim, finish releases it (or reports "nothing held" because the terminal hook already did). Both must resolve to the **main repo's** `.codebugs/`, not a worktree-local one. | A claim created in one database and a release issued against another. |
| **G6** | **No new plain `BEGIN`.** Test 24: `grep -rn 'BEGIN' src/codebugs/` yields only `db.txn` plus the two known pre-existing executable sites, `merge.py:242` and `milestones/capacity.py:182`. | A third executable `BEGIN` anywhere in `src/codebugs/`. |
| **G7** | **`--format ids` is machine-parseable and empty-safe.** Tests 25 and 26: `codebugs claims --holder <nobody> --format ids` exits **0** with **empty stdout** (test 25); with matches it prints bare ids, one per line, no header, and each round-trips into `codebugs release` (test 26). Both assert through a real process boundary. | Non-zero exit on an empty set, a header line, or an id the release verb rejects. |
| **G8** | **Mode isolation is wired.** `codebugs --mode claims claim --help` exits 0 (proves `cli.py:49`); `codebugs-mcp --mode claims` starts (proves `server.py:22-32`). | Either failing — which means the module registered but is unreachable. |
| **G9** | **The deferral is real, not asserted.** `tests/test_milestones.py:801-846` passes **unmodified**, and `pull_next` is byte-identical to today. | Any edit to that test, or any behaviour change in `pull_next`. |

### Validation measurement (non-decisional, run before implementation)

**Run the concurrency probe from Q1.** Launch two `worktree-setup.sh` invocations concurrently for one
card with different slugs and record whether both create worktrees.

**This is a measurement, not a go/no-go.** The build decision is settled by the user (§4.1); the probe
records whether the race subclass is observable in this workflow and nothing more. Record the result in
the implementation commit message either way. It has no threshold, it cannot fail, and **it does not
re-open the design decision.**

---

## 10. Deferred Follow-Ups

Each is its own commit and its own codebug. **None is a dependency of anything in the implementation
order**, and none of them appears in v1 under an alias.

| # | Item | Why deferred | What it needs |
|---|---|---|---|
| D1 | **`pull_next` / `release_item` integration** | Removes four independently-found defects at once, including pseudocode that **cannot run**: it passed `holder=agent_id` while `release_item`'s variable is `agent` (verified: `milestones/capacity.py:234-235` is `item = _get_item_by_ref(conn, item_ref)` / `agent = item.get("assigned_agent")`). Shipping a broken second consumer is worse than shipping one. | A **public** ambient-transaction API on `claims` (CLAUDE.md forbids `capacity.py` calling `_claim_core`), an `item_kind='external'` branch (those items have no resolvable entity id), and a held-entity skip policy for `pull_next`'s candidate loop. See §8 Q2. |
| D2 | **`expected_status` + `changed`** on `update_finding` / `update_requirement` (+ MCP + CLI) | Orthogonal to ownership; §5.10.2's guard is already written to be correct when it lands. **This was the literal form of the user's original question** — deferred, not dropped. | Its own design pass; it is a generic CAS for arbitrary transitions, not a claims feature. |
| D3 | **`steal`** (explicit opt-in ownership transfer) | No caller in v1. | Two statements in one transaction, with `prev_status` **carried over from the victim row** (re-reading it would make the thief's release restore to the victim's projected status, permanently pinning the entity), and `expected_holder` as a required CAS argument. |
| D4 | **Staleness verifier, divergence report, `claims audit [--prune]`** | The git predicate must be re-based onto **ancestry** first (§4.2). Deferring also removes the MCP-subprocess concern (up to 2N sequential git processes per audit) and the pre-worktree prune hazard (a concurrent `--prune` releasing a valid in-flight claim taken before its branch exists at `:143`). | An ancestry predicate (`git merge-base --is-ancestor`; `git show-ref --verify --quiet` if pure existence is ever needed — **never** `git rev-parse --verify`, which accepts `refs/heads/main~1`), a uniform cwd-failure mapping (a file-as-cwd probe raised an uncaught `NotADirectoryError` through `db.git_rev_parse`, `db.py:210-226`), a bound on rows audited, and a rule that `--prune` never touches a claim younger than the setup window. **This is where `divergent`, `stale` and `orphaned` live.** |
| D5 | **`claim-history` CLI / `claims_history` MCP**, and any `include_released` read | The data is already there (soft delete); only the surface is missing. **This is why v1's read paths return live claims only.** | Trivial once wanted. |
| D6 | **Retention** (`DELETE FROM entity_claims WHERE released_at < ?`) | ~200 bytes/row, tens per week. Round 2 promised a `prune --before` verb in prose and shipped none. | Add the verb when the table's real growth is known. |
| D7 | **`codebugs get` gains a `claim` block; `codebugs summary` gains a claims line** | Ergonomics, not correctness — but it is the fix for §5.9's disclosed bypass cost. | ~4 lines each, plus a seam so `findings.py` still does not import `claims`. |
| D8 | **`holder_kind='process'`** | Cut from the CHECK list. Adding a value later needs a table rebuild, the same shape as `reqs.py:53-95`. | A real use case. |
| D9 | **`merge.py` + `milestones/capacity.py` refactor onto `db.txn`** | Mechanical and behaviour-identical; touches modules `claims` never uses. Test 24 ratchets the allowlist so this can only shrink it. | Covered by the existing race test at `tests/test_milestones.py:801-846`. |
| D10 | **`release_item` atomicity fix** | A real bug, and **independent of claims**. It deserves its own commit, not a ride-along. | Its own investigation. |
| D11 | **`meta` read-modify-write lost update** in `update_finding` (`findings.py` re-serialises the whole JSON blob) | Correctly identified in Round 2 and correctly scoped out. | File as a codebug. |
| D12 | **`git branch -d "${BRANCH}"` in `worktree-finish.sh`** after a successful merge | Not part of this design, but found by it: branches accumulate forever (`git branch -d/-D/--delete` has **0** occurrences in that file) and each one blocks every future branch for its card via the `:86-88` guard. | Its own commit. **Interaction:** doing this makes S0's ancestry filter mostly redundant. **Do S0 first** — see §8 Q3. |
| D13 | **CLAUDE.md stale-fact fix:** the `blockers.py` debt bullet cites `db._row_to_dict()` / `reqs._row_to_dict()`; neither exists — it is the public `db.row_to_dict` (`db.py:229`). | Unrelated, found in passing. | One-line edit. |

---

## 11. Appendix — the council artifact directory

`/home/faxik/w/codebugs/docs/superpowers/plans/design-council-entity-claims/`

| File | What it is |
|---|---|
| `00-problem-brief.md` | The problem statement and nine success criteria |
| `01-architect-a.md`, `02-architect-b.md`, `03-architect-c.md` | Round-1 competing proposals |
| `04-research.md` | External research on claim/lease ledgers |
| `05-adversary-r1.md`, `06-judge-r1.md` | Round-1 attack and ruling |
| `CHECKPOINT-r1.md` | **User decision: build B1** — overrides the Judge's proportionality argument |
| `07-architect-r2.md` | Round-2 design (superseded; several claims retracted in Round 3) |
| `08-adversary-r2.md`, `09-judge-r2.md` | Round-2 attack and the scope ruling |
| `CHECKPOINT-r2.md` | **User decision: requirements get claim records, status unchanged, no CHECK rebuild** |
| `10-architect-r3.md` | Round-3 design — the base this document was folded from |
| `11-final-verifier.md` | Opus final verification (probes re-executed, shell diffs applied) |
| `FINAL-DESIGN.md` | **This document** |
| `FINAL-PLAN.md` | Files, ordered steps, testing strategy, rollback |

The Codex final verification lives outside the repo, in the session scratchpad
(`codex-final-result.md`).

**Reading order for an implementer:** this document, then `FINAL-PLAN.md`. Nothing else is required.
The Round-1 and Round-2 artifacts contain claims this design retracts, and reading them without
reading §4 first is a source of error, not of context.
