# Architect B — Round 2: The Claim Ledger, converged and implementable

**Status:** ONE design. Not options. Every Round-1 defect found against B1 by either adversary is
addressed by name in **§17**, with the fix located in the section that implements it. §17.1 lists the
Round-1 claims I am retracting.

**Scope of the change:** one new domain module (`claims.py`), one new table, one new write method on
`entities.py`, one new declarative field on `EntityKind`, one shared transaction helper in `db.py`,
one status-change hook seam in `db.py` fired by two existing modules, one new CLI verb group, one
new parameter on an existing CLI verb, and a ~65-line diff across two shell scripts in a second
repository (§13.2, §13.3) — which is the part that decides whether any of the rest matters.

---

## 0. Verification ledger — what I opened or executed THIS run

I inherit nothing on trust. Everything load-bearing below was re-verified this run. Where Round 1
was wrong, I say so.

### 0.1 Executed probes (this run, `uv run python`, repo venv)

Environment: **Python 3.13.3, SQLite 3.47.1**.

| # | Probe | Result |
|---|---|---|
| P1 | `sqlite3.SQLITE_BUSY` / `SQLITE_LOCKED` / `SQLITE_BUSY_SNAPSHOT` exist as module constants | `5` / `6` / `517` — **exist** |
| P2 | Partial `UNIQUE INDEX … WHERE released_at IS NULL` + `ON CONFLICT(entity_id) WHERE released_at IS NULL DO UPDATE … WHERE holder = excluded.holder RETURNING …, (touch_count = 1) AS was_new` | works: fresh → `(c1,'branch-a',1,was_new=1)`; same holder → `(c1,'branch-a',2,was_new=0)`; other holder → **`None`** |
| P3 | Soft release (`SET released_at=…` guarded), then re-claim by a different holder | release returns the row; re-claim inserts a **new** `claim_id`; both rows coexist, one live |
| P4 | `cursor.rowcount` on a `RETURNING` statement | `0` before fetch, `1` after — **X-2 reconfirmed** |
| P5 | `conn.in_transaction` after explicit `BEGIN IMMEDIATE` | `True`. Nested `BEGIN IMMEDIATE` → `OperationalError: cannot start a transaction within a transaction`, **`sqlite_errorname='SQLITE_ERROR'`, code 1** |
| P6 | Contention with `busy_timeout=0` | `OperationalError: database is locked`, **`sqlite_errorname='SQLITE_BUSY'`, code 5** |
| P7 | `ROLLBACK` with no active transaction | `OperationalError: cannot rollback - no transaction is active`, **`SQLITE_ERROR`, code 1** |
| P8 | Legacy mode (`isolation_level=''`): plain DML then no commit | `in_transaction` flips `False→True`; a second connection's `BEGIN IMMEDIATE` gets `SQLITE_BUSY`. **X-3 reconfirmed independently** |
| P9 | Guarded restore `UPDATE … WHERE k=? AND v=?` **without** `RETURNING` | `rowcount=1` on match, `0` on mismatch — the idiom is safe *only* without `RETURNING` |
| P10 | `expected_status` compare-and-swap shape | mismatch → `rowcount=0`; match → `rowcount=1` |

**P5/P6/P7 together retire the string-matching error classifier from Round 1.** Contention is
`SQLITE_BUSY` (5) / `SQLITE_LOCKED` (6); "cannot rollback" and "cannot start a transaction" are
`SQLITE_ERROR` (1). The design classifies on `exc.sqlite_errorcode`, not on `str(exc)`. That alone
kills SERIOUS-3 at the root rather than patching around it.

### 0.2 Repo facts re-verified by direct read (corrections to Round 1 in **bold**)

- `db.connect()` — `db.py:492-503`. Sets `journal_mode=WAL` only. **No `busy_timeout` anywhere in
  `db.py`** (grep for `BEGIN` in `db.py`: zero hits). C4 stands.
- **`db.immediate_txn` does not exist.** The `BEGIN IMMEDIATE` + `isolation_level` save/restore dance
  is hand-copied in exactly two places: `merge.py:239-289` and `milestones/capacity.py:179-218`.
- `db.register_post_add_hook` / `run_post_add_hooks` — `db.py:178-204`. Hooks run inside the
  caller's transaction before commit; `run_post_add_hooks` **swallows every exception** and writes to
  stderr (`db.py:201-204`). Fired from `findings.py:176` (`add_finding`) and `findings.py:230`
  (`batch_add_findings`).
- `db.register_schema` — `db.py:49-64`, **raises `ValueError` on duplicate registration**. The
  `register_post_add_hook` docstring's claim that its silent-return "matches register_schema
  discipline" is false. My Round 1 repeated that false claim; retracted.
- **`db._row_to_dict` does not exist. It is `db.row_to_dict`, public, at `db.py:229-240`.** The
  CLAUDE.md "known architectural debt" bullet about `blockers.py` reaching into
  `db._row_to_dict()` / `reqs._row_to_dict()` is **stale** — `blockers.py:87,307,442` call the public
  `db.row_to_dict`, and `reqs.py` defines no such helper at all. Worth a separate CLAUDE.md fix;
  out of scope here but recorded.
- `findings.update_finding` — `findings.py:235-302`. Read-modify-write on the whole row, then
  `conn.execute(f"UPDATE findings SET {…} WHERE id = ?")` at `:298`, **`conn.commit()` at `:299`**,
  then re-select. **It already supports `append_note`** (`findings.py:241`, `:270-273`).
- `findings` CLI `update` subparser — `findings.py:972-975`: positional `id`, `--status`, `--notes`.
  **That is the complete flag set.** `_cmd_update` — `findings.py:746-760` — forwards only `status`
  and `notes`. `append_note` is implemented and unreachable from the CLI.
- `reqs.update_requirement` — `reqs.py:163-224`, **same shape, same internal `conn.commit()` at
  `:223`**. Confirms SERIOUS-5: a hook in `findings` only is half a mechanism.
- `reqs` schema CHECK — `reqs.py:22-23`:
  `CHECK(status IN ('planned','partial','implemented','verified','superseded','obsolete'))`. No
  `in_progress`. Not touched by this design (SETTLED).
- `types.utc_now()` — `types.py:12-14`, `strftime("%Y-%m-%dT%H:%M:%SZ")`, whole seconds.
  `FINDING_TERMINAL = frozenset({"fixed","not_a_bug","wont_fix"})` at `types.py:36`.
- **`milestones` is a package, not a module.** `pull_next` is at
  `src/codebugs/milestones/capacity.py:167-220`; `BEGIN IMMEDIATE` at `:182`; `COMMIT` at `:213`;
  `except Exception: ROLLBACK; raise` at `:214-216`; `finally: conn.isolation_level = saved` at
  `:217-218`. There is **no top-level `capacity.py`**. Round 1 (and the brief) cited it as if there
  were.
- **`pull_next` already loops over candidates** — `for item, milestone in _candidates(conn): fail =
  _eligibility_failure(...); if fail is None: chosen = item; break`. This is the seam a
  `held_by_other` refusal drops into cleanly (§11).
- `_ensure_modules_loaded` — `db.py:478-489`, imports
  `findings, provenance, reqs, merge, sweep, bench, blockers, milestones`.
- `RETURNING` appears in `src/` exactly once: `sweep.py:313`, `RETURNING (recurrence_count = 1) AS
  was_new` — outcome-from-content, `rowcount` never consulted. **That is the in-repo precedent this
  design copies verbatim in shape.**

### 0.3 Autosorter facts re-verified — **the Round-1 line numbers are off by two**

The brief and both adversaries cite `worktree-setup.sh:120-123`, `:143`, `:208-215`. The file on
disk today (275 lines, `set -euo pipefail` at `:8`) has:

| Cited in R1 | Actual today | Content |
|---|---|---|
| `:120-123` | **`:117-125`** | the `in_progress)` arm: comment "~41 cards sit in_progress and a known share of those are stale", then `echo "⚠ …"`, **no claim** |
| `:143` | **`:141`** | `git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" …` — the irreversible act |
| `:208-215` | **`:206-212`** | `for cb in ${_claim_ids}; do if codebugs update "${cb}" --status in_progress …` |

I use the verified numbers below. Three further facts I verified myself, all load-bearing:

1. **`open)` at `:113-116` does not claim.** It appends to `_claim_ids` and the actual write happens
   at `:206-212`, i.e. **65 lines after `git worktree add`**. The gate and the write are separated by
   the irreversible act. Round 1 treated `:206` as "the claim"; it is the *record*, not the gate.
2. **Every `codebugs` invocation is `if`-guarded or `|| true`.** `:110-111` (`codebugs get`, piped
   through `sed`, `|| true`), `:206-212`, `:225-229`. `set -e` cannot fire on any of them. **A new
   `codebugs claim` that must be able to abort the script therefore has to be wired deliberately —
   the existing convention is "best-effort, never fatal", and the whole value of this design is that
   one call breaks that convention.** No proposal in Round 1 noticed this.
3. `${BRANCH_NAME}` is set at `:38` from `$1` and is in scope at `:113` — *before* `:141`. The
   identity exists at the moment the gate needs it. Confirmed by reading `:38` and the twelve
   `BRANCH_NAME` uses.
4. `worktree-finish.sh` calls **no** `codebugs` binary directly — it shells
   `tools/auto-resolve-codebugs.py` (`:1120`) and `tools/auto-mark-milestone-integration.py`
   (`:1133`), both `|| echo`-guarded. **Release-on-finish therefore has no existing shell hook to
   attach to** and must be added as a new guarded call. Round 1 assumed it did.
5. `git ls-files` in `/home/faxik/w/codebugs` filtered for `fix-latest-codebugs`: **no output**.
   Confirmed outside the repo. It does not count as adoption.

---

## 1. The question that actually decides this design

> **What makes a claim record trustworthy enough to gate on?**

This is the binding constraint, not atomicity. `worktree-setup.sh:117-125` is the proof: the script
*has* the signal, reads it, and deliberately downgrades it to a warning, because the tracker's
`in_progress` is not precise enough to refuse on. A design that makes the write more atomic and
leaves the signal equally unreadable has shipped nothing.

So: **why is `in_progress` untrustworthy?** Five reasons, all structural, none about atomicity:

| # | Defect of `status='in_progress'` | Evidence |
|---|---|---|
| D1 | **Many writers, many meanings.** An agent claiming, `provenance.py` reacting to a commit trailer, `triage.py`, a human, and the MCP tool all write the same cell for different reasons. | brief C1; `provenance.py:264-269` |
| D2 | **No holder.** You cannot ask "who?", so you cannot ask *them*. A signal you cannot escalate is a signal you learn to ignore. | no column exists anywhere for findings |
| D3 | **No liveness.** Nothing distinguishes "set 4 minutes ago by a running agent" from "set in March by an agent that died". `updated_at` is bumped by any edit, so it is not a heartbeat. | `findings.py:294` |
| D4 | **No clearer.** Nothing ever removes it. A crashed agent leaves `in_progress` forever. That is *why* there are ~41 of them. | `worktree-setup.sh:117-121` |
| D5 | **Write-only, therefore never corrected.** Because no consumer gates on it, no feedback loop has ever pressured it toward accuracy. The user's own post-mortem: "a WRITE-ONLY field that nothing reads". | `autosorter/tools/CLAUDE.md:10` |

**A claim record is gateable exactly to the degree that it repairs D1–D5. Nothing else in this
design matters as much.** Here is how each is repaired, and what remains guessy.

### L1 — One writer, one meaning (repairs D1)

`entity_claims` rows are created, mutated and closed by `claims.py` and by nothing else. There is no
"someone set this for another reason" case, because there is no other reason and no other writer. A
status can mean five things; a claim means one. This is not a property of the table, it is a property
of the *module boundary*, and it is the reason ownership must not live in a status column.

### L2 — The holder is a name you can act on (repairs D2)

`holder` is not an opaque agent nonce. The design mandates a **structured identity**:

```
holder        TEXT NOT NULL     -- e.g. 'fix-cb-2534-debug-rescue-scope'
holder_kind   TEXT NOT NULL     -- 'branch' | 'agent' | 'human' | 'process'
holder_repo   TEXT              -- abs path of the repo the branch lives in, else NULL
```

At the live call site the holder is `${BRANCH_NAME}` and the kind is `branch` — the Judge's Round-2
item 2, adopted. This is the right identity for three independent reasons, and I claim only these:
it already exists at the call site (`worktree-setup.sh:38`); it is what the *existing* git guard at
`:75-104` keys on, so the git layer and the tracker layer agree by construction instead of by
convention; and — the load-bearing one — **it is externally checkable**, which is L3.

### L3 — Liveness is a fact, not a threshold (repairs D3, and this is the core of the answer)

The user forbade TTL/lease (C8). Good — a TTL would have reproduced D3 exactly: "stale after N
hours" is a guess, and a gate built on a guess gets `--allow-duplicate`-ed by reflex, which is
precisely the failure the script's own comment predicts.

The replacement is not a better threshold. It is a **falsifiable predicate**:

> A claim held by `holder_kind='branch'` is live **iff** `git -C <holder_repo> rev-parse --verify
> refs/heads/<holder>` succeeds.

That is a fact about the world, not an inference about elapsed time. A branch that was merged and
deleted by `worktree-finish.sh` is *provably* not being worked on. A branch that exists is *provably*
still checked out somewhere. The tracker stops guessing.

Three honest qualifications, stated rather than buried:

- **The tracker does not run git.** `claims.py` stores the evidence and exposes it; verification is
  performed by the caller, which at the live call site is a shell script that already has git in
  hand and can do it in one line. `claims.audit()` accepts an **injected verifier callable**
  (default `None` → every claim reports `liveness="unverified"`), so the module has no subprocess
  dependency, is trivially testable, and never silently shells out from inside an MCP request.
- **It degrades, it does not fail.** `holder_kind='agent'` has no external referent; those claims
  report `liveness="unverifiable"` and fall back to `renewed_at` + a reader-chosen threshold, i.e.
  exactly today's precision — no worse, and explicitly labelled as the weaker signal.
- **An existing branch is not proof of an *active* agent**, only of unfinished work. That is the
  right conservatism for a gate whose false-refuse costs a `--force` and whose false-allow costs 40
  minutes.

### L4 — Three clearers, all automatic (repairs D4 — the actual cause of the 41)

The pile exists because nothing ever removes it. The design ships **three** removers, and I treat
them as load-bearing deliverables, not follow-ups:

1. **Terminal-status hook.** Any writer moving an entity to a terminal status auto-releases the claim
   inside the same transaction — including `provenance.py`'s commit-trailer flip, which is the C1
   collision, handled at the choke point rather than by asking provenance to be careful (§9).
2. **Explicit release**, wired into `worktree-finish.sh` (§12.4).
3. **`claims audit --prune`**, which closes claims whose holder branch is provably gone (L3), with a
   recorded `release_reason='audit:branch-gone'`. Never automatic; always an explicit invocation.

### L5 — The table starts empty (repairs D5, and this is the argument nobody in Round 1 made)

`in_progress` cannot be gated on today partly because it carries years of accumulated garbage that
nobody will ever audit. **`entity_claims` starts with zero rows.** From its first day it contains
only claims made by the new path, by one writer, with a holder, with clearers attached. There is no
legacy to clean and no threshold to guess.

That is the whole precision argument, and its converse is the honest risk: **the record is gateable
only for as long as the clearers keep working.** If the terminal hook is dropped in review and
`worktree-finish.sh` never releases, `entity_claims` becomes the 42nd through 82nd stale rows and
this design has reproduced the problem it was built to fix, one table over. §14 records this as the
single largest risk in the document, above every technical defect the adversaries found.

### 1.1 What this does NOT fix — stated up front

- **CB-2431 is still not prevented.** One side never touched the tracker. No tracker-side mechanism
  reaches a caller that does not call. The Judge is right and I am not going to launder that.
- **CB-2534 is already prevented, by shipped code that is not mine.** I opened
  `worktree-setup.sh:74-104` this run: the guard parses the card id out of the branch name
  (`grep -oiE 'cb-?[0-9]{3,}'`) and greps **every branch in the repo**, not worktree paths. The
  Round-1 record describes the pre-CB-2489 version. Two slugs for one card collide on that guard
  today. **Any claim that this design prevents CB-2534 is claiming credit git already earned**, and
  §13.0 states what the claim ledger actually adds over it — including the one thing that matters
  most: the guard has the *same* false-positive problem one layer up (a merged-but-undeleted branch
  trips it forever), and a claim released at merge time is the only thing that can tell those apart.
- **Everything above is contingent on the claim moving before `worktree-setup.sh:141` and the script
  obeying a refusal.** Both are in §13 as required deliverables. If that diff does not land, this
  design is advisory, and I would rather say so now than in a retro.

---

## 2. Module structure and boundaries

```
src/codebugs/
  db.py          + txn()                      shared transaction helper (§4)   ~35 lines
                 + register_status_change_hook / run_status_change_hooks (§9)  ~30 lines
                 + PRAGMA busy_timeout in connect()                             1 line
                 (db.git_rev_parse already exists at db.py:210-226 — reused, not written)
  entities.py    + EntityKind.busy_status field (declarative)                    1 line
                 + EntityRef.set_status()      the module's FIRST write path    ~20 lines
  claims.py      NEW domain module: schema, 7 public fns, 7 MCP tools, CLI     ~330 lines
  findings.py    + fires run_status_change_hooks                               ~6 lines
                 + expected_status / changed on update_finding + MCP + CLI     ~25 lines
  reqs.py        + fires run_status_change_hooks (SERIOUS-5)                   ~6 lines
                 + expected_status / changed on update_requirement             ~20 lines
  milestones/
    capacity.py  + calls claims._claim_core() inside pull_next (§11)           ~12 lines
                 + calls claims._release_core() inside release_item            ~6 lines
                 + refactor onto db.txn()                          ~15 lines, mechanical
  merge.py       + refactor onto db.txn()                          ~15 lines, mechanical
  server.py      SERVER_NAMES += {"claims": "codeclaims"}                        1 line
  cli.py         --mode choices += "claims"                                      1 line
```

### 2.1 The dependency rules I am obeying, and the one I am changing

`claims.py` imports `db`, `entities`, `types`. **It imports no domain module and no domain module
imports it, with one deliberate exception**: `milestones/capacity.py` imports `claims` (§11).
`milestones` already imports `findings` and `reqs`, so this adds no new layer — it uses the same
public-interface-only discipline CLAUDE.md mandates.

`findings.py` and `reqs.py` do **not** import `claims`. They call `db.run_status_change_hooks(...)`;
`claims.py` registers into it at module level. The direction of the dependency is
`claims → db ← findings`, never `findings → claims`. This is the identical shape as the existing
`register_post_add_hook` seam, where `milestones.auto_route_finding` reaches `findings.add_finding`
without `findings` knowing `milestones` exists.

**The rule I am changing, explicitly and with the reason stated:**

> `entities.py` becomes read **and write**, gaining exactly one write method, `EntityRef.set_status`.

Round 1 I kept it read-only and paid for it with a projector-callback registry that each projecting
domain had to implement — which the adversary correctly scored as failing criterion 4
("third kind by declaration only"). The registry was elegance bought with a criterion. I am
reversing that trade. The justification is that `entities.py`'s own docstring already claims the
role — *"Owns the one sanctioned cross-table read over `findings` / `requirements`… Adding a new
entity kind is a single entry in `ENTITY_KINDS`"* (`entities.py:4-7`) — and the *only* way to keep
the second half of that sentence true for projection is to put the write where the read already is.
The alternative, a per-domain callback, means adding a kind is an entry **plus** a callback **plus**
a registration, which is not "a single entry".

Cost, stated honestly: `entities.py` grows a second interpolated-identifier statement next to
`_read`'s (`entities.py:86`, which already carries `# noqa: S608`). That is **one new interpolation
site, in the module that already owns the only other one**, guarded by the same
`readable_cols`/`ENTITY_KINDS` closed-world constraint. Codex's objection to Architects A and C on
this ground was that they added a *second copy* of registry interpolation in a *different* module;
that objection does not transfer. CLAUDE.md's module-boundary rule gets an explicit amendment naming
`entities.py` as the sanctioned cross-table read **and status write** — because an undocumented
exception is exactly the kind of debt that section already lists.

A correction to Round 1 while I am here: `_SAFE_IDENT` (`entities.py:20`) is **defined and never
referenced** in production code. The actual guard at `entities.py:83-84` is the `readable_cols`
membership test. Round 1 (mine and C's) described `_SAFE_IDENT` as the guard; it is not. `set_status`
therefore guards the same way `_read` does — against a frozen `ENTITY_KINDS`, never against caller
input.

---

## 3. Schema

`claims.py`, module-level `CLAIMS_SCHEMA`, registered with `db.register_schema("claims", ensure_schema)`.

```sql
CREATE TABLE IF NOT EXISTS entity_claims (
    claim_id       TEXT PRIMARY KEY,          -- 'CLM-<n>', generated like findings._next_id
    entity_id      TEXT NOT NULL,             -- 'CB-1234' | 'FR-7' | future kinds
    kind           TEXT NOT NULL,             -- EntityKind.name — a VALUE, never an identifier

    holder         TEXT NOT NULL,             -- 'fix-cb-2534-debug-rescue-scope'
    holder_kind    TEXT NOT NULL DEFAULT 'agent'
                     CHECK(holder_kind IN ('branch','agent','human','process')),
    holder_repo    TEXT,                      -- abs path of the repo owning the branch, else NULL

    claimed_at     TEXT NOT NULL,
    renewed_at     TEXT NOT NULL,             -- heartbeat: bumped free on every already_mine
    touch_count    INTEGER NOT NULL DEFAULT 1,-- monotone; THE outcome discriminator (§7)
    note           TEXT NOT NULL DEFAULT '',

    prev_status    TEXT,                      -- pre-claim status, NULL if not projected
    projected_to   TEXT,                      -- status we wrote, NULL if not projected

    released_at    TEXT,                      -- NULL == live. Soft delete.
    released_by    TEXT,                      -- who/what closed it
    release_reason TEXT                       -- 'explicit' | 'terminal:<status>' | 'stolen:<holder>'
                                              -- | 'audit:branch-gone'
);

-- THE mutual-exclusion primitive: at most one LIVE claim per entity.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_live
    ON entity_claims(entity_id) WHERE released_at IS NULL;

-- criterion 3, reverse direction: "what does agent-7 hold" — indexed point query, no fold.
CREATE INDEX IF NOT EXISTS idx_claims_holder_live
    ON entity_claims(holder) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_claims_entity   ON entity_claims(entity_id);
CREATE INDEX IF NOT EXISTS idx_claims_kind_live
    ON entity_claims(kind) WHERE released_at IS NULL;
```

### 3.1 Why `claim_id` PK + partial unique index, and not `entity_id` PK

Round 1 made `entity_id` the primary key and then claimed the table was "a strict column subset" of
a history-bearing design, upgradable with `ALTER TABLE ADD COLUMN`. **That was false and I retract
it** (SERIOUS-6): a PK on `entity_id` structurally forbids more than one row per entity, and SQLite
cannot drop or replace a primary key with `ALTER TABLE` — it requires a full table rebuild.

The fix is not to promise a migration; it is to not need one. `claim_id TEXT PRIMARY KEY` plus
`CREATE UNIQUE INDEX … WHERE released_at IS NULL` gives **exactly the same exclusion guarantee**
while allowing an unbounded number of *closed* rows per entity. Verified by execution this run (P2,
P3): the upsert with a partial conflict target behaves identically to a PK upsert for exclusion, and
after a soft release a different holder inserts a **new row** with a new `claim_id` while the old row
survives with its `released_at` set.

This one change also retires SERIOUS-7. Round 1 promised the C1 handler would "record an auto-release"
into a table that only held current claims and whose release was a `DELETE` — an audit fact with
nowhere to live. Now the fact has a home: the row stays, `released_at` / `released_by` /
`release_reason` are written, and `release_reason='terminal:fixed'` is a *queryable record* that the
commit trailer closed the claim. **This is not scope creep to buy history for its own sake — it is
what makes L4 auditable.** "Are the clearers working?" becomes
`SELECT release_reason, count(*) FROM entity_claims WHERE released_at IS NOT NULL GROUP BY 1`, and
the answer to that query is the only evidence that will ever tell us whether §1's precision argument
survived contact.

Retention: none in v1. A closed claim row is ~200 bytes and the realistic rate is tens per week.
`claims prune --before <iso>` exists as an explicit CLI verb; nothing runs automatically. If this
ever matters, it is a `DELETE` with a `WHERE released_at < ?`, not a migration.

### 3.2 No `REFERENCES`, on purpose

`PRAGMA foreign_keys` is OFF (brief C5), so a `REFERENCES findings(id)` would read as enforced and
would not be. `milestone_items.milestone_id … REFERENCES milestones(id)`
(`milestones/_schema.py:54`) is already decorative; I decline to add a second. Integrity comes from
three real mechanisms instead:

1. **Write-time validation.** `claim()` calls `EntityRef.of(entity_id).require(conn)`
   (`entities.py:105-108`) before any insert. Bad format → `ValueError` (`entities.py:78`); well-formed
   but absent → `KeyError`. Matches CLAUDE.md's error contract. A claim on a non-existent entity
   cannot be created through the API.
2. **Read-time orphan reporting.** `who_holds` / `held_by` / `list_claims` evaluate
   `EntityRef.of(row["entity_id"]).exists(conn)` and emit `"orphaned": true`. Orphans are reported,
   never silently dropped.
3. **Precedent.** `milestones/triage.py` already catches `KeyError` for a deleted finding rather
   than relying on cascade. This is the house style.

---

## 4. Transaction discipline — `db.txn()` and the ambient-transaction rule

This section is the fix for **FATAL-1** and **SERIOUS-3**. Two mechanisms, and a rule about which
functions may use which.

### 4.1 `db.txn` — reentrant, never a plain `BEGIN`, never masks the original exception

```python
# db.py — infrastructure. No domain import. Replaces the hand-copied dance in
# merge.py:239-289 and milestones/capacity.py:179-218.

@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[bool]:
    """BEGIN IMMEDIATE with isolation_level save/restore, reentrant.

    Yields True if THIS frame opened the transaction and will commit it;
    False if a transaction was already open, in which case this frame does
    nothing at all — no BEGIN, no COMMIT, no ROLLBACK — and the owning frame
    keeps full control of the outcome.

    Never write a plain `BEGIN` in this codebase: it pins a read snapshot and
    the later upgrade dies with SQLITE_BUSY_SNAPSHOT, which busy_timeout
    cannot rescue (research C3).
    """
    if conn.in_transaction:
        yield False                     # ambient: the caller owns it
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
                    pass                 # never let cleanup replace the real exception
            raise
    finally:
        conn.isolation_level = saved
```

Three deliberate details, each answering a specific executed failure:

- **`if conn.in_transaction: yield False`** — P5 confirms `in_transaction` is `True` after an
  explicit `BEGIN IMMEDIATE`, so `pull_next`'s open transaction is detected. Nesting cannot occur.
- **`if conn.in_transaction` guarding the `ROLLBACK`, plus a swallowed `OperationalError`** — this is
  SERIOUS-3, killed at the root. P7: a `ROLLBACK` with no active transaction raises
  `SQLITE_ERROR` (code 1), whose message contains neither "locked" nor "busy". Round 1's handler
  would have let that new exception replace the original and then re-raised it past the
  `undetermined` classifier. Here the cleanup can never become the reported error.
- **`except BaseException`** — a `KeyboardInterrupt` mid-transaction must still roll back.

**Invariant, to be stated in `CLAUDE.md` and enforced by a test:** the codebase contains no plain
`BEGIN`. `db.txn` is the only place `BEGIN IMMEDIATE` is written. Verified today: `grep -n BEGIN
src/codebugs/db.py` → zero hits; the only two occurrences in `src/` are `merge.py:242` and
`milestones/capacity.py:182`, both refactored onto `txn` in this PR (mechanical, behaviour-identical,
covered by the existing race test at `tests/test_milestones.py:801-846`).

The reentrancy is belt-and-braces. **The primary defence against nesting is §4.2, which arranges
for nesting never to be attempted.**

### 4.2 Core / wrapper split — the actual FATAL-1 fix

Every claim operation exists at two layers:

| Layer | Naming | Transaction | May commit? | Callers |
|---|---|---|---|---|
| **core** | `_claim_core`, `_release_core`, `_steal_core` | none — emits statements only | **NEVER** | other modules already inside a transaction (`milestones/capacity.py`) |
| **public** | `claim`, `release`, `steal` | `with db.txn(conn)` | via `txn` | MCP tools, CLI, external callers |

```python
def claim(conn, *, entity_id, holder, ...) -> dict[str, Any]:
    try:
        with db.txn(conn):
            return _claim_core(conn, entity_id=entity_id, holder=holder, ...)
    except sqlite3.OperationalError as exc:
        return _undetermined(exc)       # §5.2 — classified on sqlite_errorcode
```

`_claim_core` has the same contract Round 1 gave `_project_claim`, and the adversary called that
contract a genuine strength: **runs inside the caller's transaction and MUST NOT commit.** It is now
the contract of the whole core layer, not of one helper. This is the adversary's own suggested repair
("splitting the primitive into a statement-emitting core and a transaction-managing wrapper"),
chosen over a `manage_txn: bool` flag because a boolean parameter puts the hazard in the call site's
hands, whereas two names put it in the type system's.

**Enforcement, not just documentation:** a test opens a transaction, calls every `_*_core` function,
and asserts `conn.in_transaction` is still `True` afterwards. A core function that commits fails that
test.

### 4.3 `busy_timeout`, explicitly

`db.connect()` (`db.py:492-503`) sets `journal_mode=WAL` and nothing else; the 5000 ms that makes a
loser get a clean result rather than an exception is inherited from `sqlite3.connect(timeout=5.0)`'s
default and appears nowhere in the source. One line, its own behaviour-neutral commit, ahead of
everything else here (the Judge and both adversaries independently asked for this):

```python
conn.execute("PRAGMA busy_timeout=5000")   # explicit; was inherited from sqlite3's default
```

`claims.py` does not re-set it. One owner for the setting.

---

## 5. Public API

### 5.1 `claims.py` — every signature

Keyword-only after `conn`, per CLAUDE.md. Type hints on every public signature.

```python
# --- core layer: emits statements, NEVER commits, NEVER opens a transaction ---
def _claim_core(conn, *, entity_id: str, holder: str, holder_kind: str = "agent",
                holder_repo: str | None = None, note: str = "",
                project: bool = True, allow_terminal: bool = False) -> dict[str, Any]
def _release_core(conn, *, entity_id: str, holder: str, restore_status: bool = True,
                  reason: str = "explicit", released_by: str | None = None) -> dict[str, Any]
def _steal_core(conn, *, entity_id: str, holder: str, expected_holder: str, reason: str,
                holder_kind: str = "agent", holder_repo: str | None = None,
                project: bool = True) -> dict[str, Any]

# --- public layer: transaction-managing, contention-classifying ---
def claim(conn, *, entity_id: str, holder: str, holder_kind: str = "agent",
          holder_repo: str | None = None, note: str = "",
          project: bool = True, allow_terminal: bool = False) -> dict[str, Any]
def release(conn, *, entity_id: str, holder: str, restore_status: bool = True,
            reason: str = "explicit") -> dict[str, Any]
def steal(conn, *, entity_id: str, holder: str, expected_holder: str, reason: str,
          holder_kind: str = "agent", holder_repo: str | None = None,
          project: bool = True) -> dict[str, Any]

# --- read layer: no transaction, no writes ---
def who_holds(conn, *, entity_id: str) -> dict[str, Any] | None
def held_by(conn, *, holder: str) -> dict[str, Any]
def list_claims(conn, *, kind: str | None = None, holder: str | None = None,
                holder_kind: str | None = None, divergent_only: bool = False,
                stale_after_seconds: int | None = None,
                include_released: bool = False, limit: int = 200) -> dict[str, Any]
def history(conn, *, entity_id: str, limit: int = 50) -> dict[str, Any]

# --- audit layer: the L3 liveness check ---
Verifier = Callable[[str, str | None], str]   # (holder, holder_repo) -> 'live'|'gone'|'unverifiable'

def audit(conn, *, verifier: Verifier | None = None, prune: bool = False,
          actor: str = "audit") -> dict[str, Any]
def git_branch_verifier(holder: str, holder_repo: str | None) -> str
```

### 5.2 Error handling

Three tiers, matching CLAUDE.md's contract exactly.

| Condition | Behaviour |
|---|---|
| Unparseable entity id | `ValueError` from `EntityRef.of` (`entities.py:78`) — propagates |
| Well-formed id, no such row | `KeyError` from `EntityRef.require` (`entities.py:105-108`) — propagates |
| SQLite contention | caught, returned as `outcome="undetermined"` |
| Anything else | propagates |

MCP tools let all of it reach FastMCP. CLI handlers catch `ValueError`/`KeyError`, print to stderr,
`sys.exit(1)` — the `_cmd_update` / `_cmd_get` pattern at `findings.py:756-758` / `:810-812`.

The contention classifier — **codes, not strings**, per P5/P6/P7:

```python
_CONTENTION = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})   # 5, 6

def _is_contention(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in _CONTENTION      # masks extended codes: 517 & 0xFF == 5

def _undetermined(exc: sqlite3.OperationalError) -> dict[str, Any]:
    if not _is_contention(exc):
        raise exc                            # a real error is never masked as contention
    return {"outcome": "undetermined", "reason": "database_busy",
            "retry_after_ms": 250, "detail": str(exc)}
```

`SQLITE_BUSY_SNAPSHOT` (517) masks to 5 and is classified as contention. `cannot start a
transaction` and `cannot rollback` are `SQLITE_ERROR` (1) and are **re-raised**, which is correct:
those are programming errors and must be loud.

**`undetermined` means: the database was too contended to tell you. The claim may or may not have
been made. Re-issue the identical call.** It is safe because the primitive is idempotent — the same
`(entity_id, holder)` pair replayed converges on `already_mine` and can never double-claim. That is
the entire reason the claim is an idempotent upsert rather than a bare `INSERT`.

---

## 6. Exact SQL, every path

### 6.1 Claim

**Guard first** (inside the same transaction, before the upsert):

```python
ref = entities.EntityRef.of(entity_id)      # ValueError on bad format
ref.require(conn)                           # KeyError if absent
busy = ref.kind.busy_status                 # declarative; None == this kind does not project
if project and busy is not None:
    current = ref.status(conn)
    if current in ref.kind.terminal and not allow_terminal:
        return {"outcome": "entity_terminal", "entity_id": entity_id,
                "current_status": current, "holder": None}
```

**The upsert — one statement, verified by execution (P2):**

```sql
INSERT INTO entity_claims
    (claim_id, entity_id, kind, holder, holder_kind, holder_repo,
     claimed_at, renewed_at, touch_count, note, prev_status, projected_to,
     released_at, released_by, release_reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, NULL, NULL)
ON CONFLICT(entity_id) WHERE released_at IS NULL DO UPDATE SET
       renewed_at  = excluded.renewed_at,
       touch_count = entity_claims.touch_count + 1,
       note        = CASE WHEN excluded.note <> '' THEN excluded.note ELSE entity_claims.note END
 WHERE entity_claims.holder = excluded.holder
RETURNING claim_id, holder, claimed_at, renewed_at, touch_count,
          prev_status, projected_to,
          (touch_count = 1) AS was_new;
```

Executed results, this run:

| Case | `RETURNING` row | Outcome |
|---|---|---|
| no live claim | `(c1,'branch-a',…,touch_count=1,was_new=1)` | `claimed` |
| live claim, same holder | `(c1,'branch-a',…,touch_count=2,was_new=0)` | `already_mine`, `renewed_at` just moved |
| live claim, other holder | **`None`** (the `DO UPDATE … WHERE` fails) | `held_by_other` |
| `sqlite_errorcode & 0xFF ∈ {5,6}` | — | `undetermined` |

**`was_new` is computed in the row, exactly like `sweep.py:313`.** `cursor.rowcount` is never read on
this or any other `RETURNING` statement in this design — see §6.5.

**On `held_by_other`, name the holder** — one parameterized read, same transaction:

```sql
SELECT claim_id, holder, holder_kind, holder_repo, claimed_at, renewed_at,
       touch_count, note, projected_to
  FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;
```

**On `claimed`, project** (only if `busy is not None` and `project`), then write the snapshot back:

```python
moved = ref.set_status(conn, new_status=busy, expected=current)   # §8, guarded, no RETURNING
conn.execute(
    "UPDATE entity_claims SET prev_status = ?, projected_to = ? WHERE claim_id = ?",
    (current, busy if moved else None, claim_id),
)
```

If `moved` is False, another writer changed the status between the guard read and the projection
inside the same `BEGIN IMMEDIATE` — which cannot happen from another *process* (we hold the write
lock) but can from a hook on the same connection. `projected_to` stays NULL, so release will not try
to restore, and the response carries `projected: false, projection: "raced"`.

`claim_id` generation follows `findings._next_id` (`findings.py:117-130`) verbatim in shape:
`SELECT id … ORDER BY CAST(SUBSTR(id, 5) AS INTEGER) DESC LIMIT 1` over `CLM-%`, inside the same
`BEGIN IMMEDIATE`, so the read-then-insert is not a race.

### 6.2 Release — and the answer to Q5

```sql
UPDATE entity_claims
   SET released_at = ?, released_by = ?, release_reason = ?
 WHERE entity_id = ? AND holder = ? AND released_at IS NULL
RETURNING claim_id, prev_status, projected_to;
```

`cur.fetchone()` — **never `rowcount`**.

| Result | Outcome |
|---|---|
| row | `released` |
| `None`, and a live claim exists for a different holder | `not_yours` (response names the real holder) |
| `None`, and no live claim exists | `not_claimed` |

Then, **only if `projected_to IS NOT NULL` and `restore_status`**, restore with a guard:

```python
restored = ref.set_status(conn, new_status=prev_status, expected=projected_to)
```

which is `UPDATE <table> SET status=?, updated_at=? WHERE id=? AND status=?` — no `RETURNING`, so
`rowcount` is the correct and verified idiom (P9). **If the holder already moved the finding to
`fixed`, the guard's `status = projected_to` fails, `rowcount == 0`, and the status is left alone.
Release never resurrects finished work.** The response reports
`status_restored: false, current_status: "fixed"`.

### 6.3 Steal — explicit opt-in only, and the FATAL-2 fix

A steal is **two statements**, not one, because the record must survive:

```sql
-- 1. close the incumbent, compare-and-swap on the holder the caller actually observed
UPDATE entity_claims
   SET released_at = ?, released_by = ?, release_reason = ?     -- 'stolen:<new holder>'
 WHERE entity_id = ? AND holder = ? AND released_at IS NULL
RETURNING claim_id, prev_status, projected_to;
```

```python
victim = cur.fetchone()          # <-- FATAL-2: FETCH. Never cur.rowcount.
if victim is None:
    live = _live_claim(conn, entity_id)
    return {"outcome": "held_by_other" if live else "not_claimed",
            "holder": live["holder"] if live else None, ...}
```

```sql
-- 2. insert the thief's claim; the partial unique index is now free
INSERT INTO entity_claims (claim_id, entity_id, kind, holder, holder_kind, holder_repo,
                           claimed_at, renewed_at, touch_count, note,
                           prev_status, projected_to, released_at, released_by, release_reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, NULL, NULL);
```

`prev_status` is **carried over from the victim row**, not re-read — otherwise the thief's release
would restore to `in_progress` (the status the victim projected) instead of the pre-claim `open`,
permanently pinning the finding. Round 1 did not say this; it is a real defect that only appears once
release is guarded. `projected_to` is likewise inherited when the victim had projected, so no second
projection write is needed.

Requiring `expected_holder` makes accidental stealing impossible: you cannot steal without having
first looked, and if the claim changed hands between your look and your steal, statement 1 returns
`None` and you are told `held_by_other` **without having written anything**.

Both statements run inside one `db.txn`.

### 6.4 Read paths — criterion 3, two indexed point queries

```sql
-- who_holds(CB-1234): uses idx_claims_live (partial unique)
SELECT * FROM entity_claims WHERE entity_id = ? AND released_at IS NULL;

-- held_by('fix-cb-2534-…'): uses idx_claims_holder_live. No window fold. No 752 ms.
SELECT * FROM entity_claims WHERE holder = ? AND released_at IS NULL ORDER BY claimed_at;

-- history(CB-1234): full lifecycle, closed rows included
SELECT * FROM entity_claims WHERE entity_id = ? ORDER BY claimed_at DESC LIMIT ?;

-- list_claims: composable, all filters optional
SELECT * FROM entity_claims
 WHERE (? IS NULL OR kind = ?)
   AND (? IS NULL OR holder = ?)
   AND (? IS NULL OR holder_kind = ?)
   AND (:include_released OR released_at IS NULL)
 ORDER BY renewed_at DESC LIMIT ?;
```

Every read row is decorated in Python with:

- `held_seconds` — `now - claimed_at`
- `idle_seconds` — `now - renewed_at` (the honest staleness signal for `holder_kind != 'branch'`)
- `stale` — `idle_seconds > stale_after_seconds`, **only when the caller supplied a threshold**.
  Never a baked-in default. The reader chooses; the tracker does not guess.
- `orphaned` — `not EntityRef.of(entity_id).exists(conn)` (§3.2)
- `divergent` — `EntityRef.of(entity_id).is_resolved(conn)` and the claim is live. **This is the
  read-time safety net for a hook that failed** (§9).

`divergent_only=True` makes "show me every claim the clearers missed" a single call. That query is
the health check for §1's L4, and it is why the design can be audited rather than believed.

### 6.5 The `RETURNING` audit — every statement in this design

FATAL-2 demanded an audit of every `RETURNING` statement, so here it is, exhaustively:

| # | Statement | `RETURNING`? | Outcome read from | Safe? |
|---|---|---|---|---|
| 1 | claim upsert (§6.1) | yes | `cur.fetchone()`, incl. computed `was_new` | ✅ |
| 2 | held_by_other lookup (§6.1) | no | `fetchone()` | ✅ |
| 3 | `UPDATE entity_claims SET prev_status…` (§6.1) | no | not consulted | ✅ |
| 4 | `EntityRef.set_status` (§8) | **no, deliberately** | `cur.rowcount` — valid only because there is no `RETURNING` (P9) | ✅ |
| 5 | release soft-delete (§6.2) | yes | `cur.fetchone()` | ✅ |
| 6 | steal statement 1 (§6.3) | yes | `cur.fetchone()` | ✅ |
| 7 | steal statement 2 (§6.3) | no | not consulted | ✅ |
| 8 | audit prune (§10) | yes | `cur.fetchall()` | ✅ |

**Rule for the implementation, to be stated in `CLAUDE.md`:** *a statement either carries `RETURNING`
and its outcome is read by fetching, or it carries no `RETURNING` and its outcome is read from
`rowcount`. Never both.* P4 is the proof: `rowcount` is `0` before a `RETURNING` cursor is fetched
and only correct after exhaustion. Round 1's `steal` mixed the idioms and would therefore have
reported `held_by_other` **while having performed the write** — strictly worse than a no-op. The
in-repo precedent for the correct idiom already existed at `sweep.py:313` and no architect cited it.

---

## 7. Outcome vocabulary — complete

```
claim    → claimed | already_mine | held_by_other | entity_terminal | undetermined
release  → released | not_yours | not_claimed | undetermined
steal    → stolen | held_by_other | not_claimed | undetermined
```

Every response dict carries: `outcome`, `entity_id`, `kind`, `holder`, `holder_kind`, `holder_repo`,
`claim_id`, `claimed_at`, `renewed_at`, `touch_count`, `held_seconds`, `idle_seconds`, `projected`,
`projected_to`, `prev_status`, `orphaned`, `divergent`. `undetermined` additionally carries
`reason`, `retry_after_ms`, `detail`. `entity_terminal` additionally carries `current_status`.

### 7.1 The discriminator is `touch_count`, never a timestamp

`types.utc_now()` (`types.py:12-14`) formats to whole seconds, so two calls inside the same second
produce equal strings and a `claimed_at == renewed_at` discriminator misreports a retry as a fresh
claim. A retrying agent on a 250 ms loop does exactly this. `touch_count` is a monotone integer
incremented by the upsert itself and is clock-independent. Verified this run (P2):
`was_new` came back `1` then `0` on two calls in the same wall-clock second.

This is unchanged from Round 1 and is SETTLED. `utc_now` itself is not modified — changing a
timestamp format used by nine modules to fix one discriminator is the wrong trade.

### 7.2 `entity_terminal` — the fifth outcome, and why it earns its place

Claiming a `fixed` finding is not a coordination event, it is a mistake. The live script already
knows this and works around it in shell — `worktree-setup.sh:203-205`: *"Only `open` cards are
flipped (decided during the guard above) — a follow-up branch on a `fixed` card must not silently
reopen it."* That rule lives in a bash comment and a `case` statement today.

Encoding it in `claim()` lets the shell **delete** its `codebugs get | sed` pre-read at `:110-111` —
which is a textbook check-then-act race, in shell, unguarded, and is the exact race this council
exists to close. **So the design removes a race from the live call site rather than only adding a
mechanism beside it.** `--allow-terminal` exists for the deliberate case (re-opening a regression),
and it records the claim without projecting.

---

## 8. The projection contract

### 8.1 Declarative, not a callback — the MEDIUM-8 fix

Round 1 required each projecting domain to implement and register a `Projector` callback, which the
adversary correctly scored as failing criterion 4 ("adding a third kind requires no new ownership
code — only a declaration"). Retracted. Projection is now **one optional field on the existing
frozen descriptor**:

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

Criterion 4 is now met **literally**: a third kind is one `EntityKind` entry. If it declares
`busy_status`, it projects; if it does not, it does not. No callback, no registration, no new
ownership code, and `claims.py` branches on nothing — it reads `ref.kind.busy_status`.

Criterion 5 is met the same way: `requirements` declares no `busy_status`, so it gets full ownership
(claim, release, steal, query, audit, history) with **no `reqs.py:22-23` CHECK-constraint rebuild**,
per the SETTLED ruling. `busy_status` defaulting to `None` also means the field is backward-compatible
with every existing `EntityKind(...)` construction — a frozen dataclass with a defaulted trailing
field breaks nothing.

### 8.2 `EntityRef.set_status` — the one sanctioned status write

```python
def set_status(self, conn: sqlite3.Connection, *, new_status: str, expected: str) -> bool:
    """Guarded status write. THE single sanctioned cross-table status write.

    Runs inside the caller's transaction and MUST NOT commit — the caller
    composes it with other writes. Returns True iff the row moved.

    Deliberately does NOT use RETURNING: rowcount is the correct outcome
    idiom precisely when RETURNING is absent (see CLAUDE.md 'RETURNING rule').
    Deliberately does NOT fire status-change hooks — see the invariant below.
    """
    cur = conn.execute(
        f"UPDATE {self.kind.table} SET status = ?, updated_at = ? WHERE id = ? AND status = ?",  # noqa: S608
        (new_status, t.utc_now(), self.id, expected),
    )
    return cur.rowcount == 1
```

Interpolation safety: `self.kind.table` comes from the frozen `ENTITY_KINDS` tuple
(`entities.py:36-55`) and can never be caller input — the same closed-world argument that already
licenses `entities.py:86`'s `# noqa: S608`. **Correction to Round 1:** `_SAFE_IDENT`
(`entities.py:20`) is defined and never referenced; the real guard is the frozen registry. I described
it wrongly in Round 1 and so did Architect C.

**Invariant — no hook, therefore no recursion.** `set_status` is called only by `claims.py`, only
with `kind.busy_status` (never terminal by construction) or with a restored `prev_status` that was
non-terminal at claim time (guaranteed by §6.1's `entity_terminal` guard). Therefore no `set_status`
call can ever produce a terminal status, therefore the auto-release hook has nothing to react to, and
`claim → project → hook → release → restore → hook → …` is unreachable. A test asserts it: a claim
followed by a release must fire zero status-change hooks.

**Why not route through `update_finding`?** Because `findings.py:299` commits. Round 1 caught this
and the adversary called it the design's standout strength; it is preserved verbatim. `set_status`
is the reason the claim and the projection land in one transaction or neither.

### 8.3 Bypass cost, stated

`set_status` writes `status` and `updated_at` and nothing else. It does not append a note, does not
touch `meta`, and does not fire the status-change hook. That is a real bypass of
`update_finding`'s side effects, and it is deliberate on all three counts. The audit trail for a
projection lives in `entity_claims` (`prev_status`, `projected_to`, `claimed_at`, `release_reason`),
which is a better record than a free-text note appended to a JSON blob.

One consequence I will not hide: a projection does **not** appear in the finding's notes history. An
operator reading only `codebugs get CB-1234` sees the status change with no explanation. The fix is
that `codebugs get` gains a `claim` block in its response (§13.5, 4 lines) — cheap, and it makes the
ownership record visible exactly where someone is already looking.

---

## 9. The status-change hook seam — C1, and the SERIOUS-4 / SERIOUS-5 answer

### 9.1 What it is

```python
# db.py — infrastructure, no domain import. Symmetric with register_post_add_hook (db.py:178-204).

def register_status_change_hook(
    name: str,
    fn: Callable[[sqlite3.Connection, str, str | None, str], None],
) -> None:
    """Register a hook that runs whenever an entity's status is changed through
    a domain update function. Hooks run inside the caller's transaction, before
    the final commit, so the status change and any hook side-effects land
    atomically. Name-keyed: module re-import is a no-op."""

def run_status_change_hooks(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Invoke every registered hook. Failures are written to stderr, never raised —
    a status write must always succeed.

    Published seam: called by findings.update_finding and reqs.update_requirement
    immediately before their conn.commit()."""
```

**Fired from two places, not one** (SERIOUS-5):

- `findings.update_finding`, between `findings.py:298` (the UPDATE) and `:299` (the commit)
- `reqs.update_requirement`, between `reqs.py:222` (the UPDATE) and `:223` (the commit)

Both only when `status is not None` and the resolved new status differs from the row's prior status
(available for free — both functions already `SELECT *` at the top, `findings.py:252` / `reqs.py:178`).

`claims.py` registers exactly one hook at module level:

```python
def _auto_release_on_terminal(conn, entity_id, old_status, new_status) -> None:
    ref = entities.EntityRef.of(entity_id)          # ValueError → swallowed by the runner
    if new_status not in ref.kind.terminal:
        return
    _release_core(conn, entity_id=entity_id, holder=_live_holder(conn, entity_id),
                  restore_status=False,             # the entity is FINISHED; never restore
                  reason=f"terminal:{new_status}", released_by="hook:status_change")

db.register_status_change_hook("claims_auto_release", _auto_release_on_terminal)
```

`restore_status=False` is load-bearing: a terminal status is the *point*, and restoring `prev_status`
would undo the finding's resolution. This is the same guard-based reasoning as §6.2, made explicit
rather than left to the guard.

### 9.2 The C1 sequence, traced end to end

Agent claims CB-1234 → `entity_claims` row live, `findings.status='in_progress'`,
`prev_status='open'`, `projected_to='in_progress'`. The agent commits `Fixes: CB-1234`.
`provenance.py:255-271` runs: `_VERB_ACTIONS` gives `status_input='fixed'`; `:261` checks
`current["status"] in types.FINDING_TERMINAL` — `in_progress` is **not** terminal (`types.py:36`) —
so it does not skip; `:265-270` calls `update_finding(status='fixed', append_note=…)`.

`update_finding` now fires `run_status_change_hooks(conn, 'CB-1234', 'in_progress', 'fixed')` before
its commit at `:299`. `claims._auto_release_on_terminal` soft-closes the claim with
`release_reason='terminal:fixed'`, inside the same transaction as the status write. Final state:
CB-1234 `fixed`, unclaimed, and the record says the commit trailer closed it.

**Note what this does NOT do:** it does not make `provenance.py` claim-aware, and it does not stop
provenance from resolving a finding held by someone else. That is correct — a commit that fixes a bug
fixes it regardless of who held the card. Round 1 said "handled at the choke point, not by asking
provenance to be careful", and that framing survives verification.

Also verified: `provenance.py:172` maps `tightens` to `status_input=None`, so a partial-progress
trailer never calls `update_finding(status=…)`, never fires the hook, and never frees a claim. (This
was Codex's FATAL against Architect C; my design is not exposed to it, and I checked rather than
assumed.)

### 9.3 Justifying new `db.py` infrastructure — SERIOUS-4, answered on the merits

The objection is fair: `db.py`'s charter is already carrying documented debt, and Round 1 justified
this seam with one consumer. Five things I did not say in Round 1, in descending order of weight:

1. **It is the ONLY mechanism in the entire council that reaches the live call site.** Verified this
   run: `worktree-setup.sh:206` → `codebugs update <id> --status in_progress` → CLI `_cmd_update`
   (`findings.py:746-760`) → `update_finding` → `findings.py:298`. Every proposal's headline adoption
   plan was a `SKILL.md` edit in a file `git ls-files` does not know. The hook is wired into the code
   path that is *already carrying the traffic*, today, without anyone changing their habits. The
   Opus adversary found this and scored it as a strength I failed to claim. I am claiming it.
2. **It is not new infrastructure; it is the missing half of existing infrastructure.** `db.py`
   already owns `register_post_add_hook` (`db.py:178-204`) for the create side, with an identical
   contract (runs in the caller's transaction, before commit, exceptions swallowed) and an identical
   consumer shape (`milestones.auto_route_finding`). The update side simply does not exist yet. The
   asymmetry is the anomaly; the hook removes it.
3. **Two producers, not one.** `findings` and `reqs` both fire it (SERIOUS-5's fix), so it is a real
   seam over the entity layer rather than a findings-specific callback wearing a general name.
4. **It is the only correct answer to C1.** Any alternative that lives in `claims.py` is polling; any
   alternative that lives in `provenance.py` handles one of the four writers.
5. **The alternative was considered and rejected on architecture, not effort.** `findings.py` could
   import `claims` and call it directly. Rejected because it inverts the layering — `findings` is
   lower than `claims` — and because `codebugs --mode findings` would then load the claims module
   unconditionally, defeating the mode system.

**The disclosed cost, and the fallback if reviewers still say no.** Exceptions are swallowed
(`db.py:201-204`'s policy, copied deliberately: a failed auto-release must never break a status
write). The degraded state is a live claim on a terminal entity. That state is **not invisible** —
it is exactly what `divergent` reports (§6.4), what `claims list --divergent` enumerates, and what
`claims audit` counts. **If the seam is rejected in review, the design degrades to read-time
divergence reporting: visible and manually recoverable, rather than silently wrong.** It costs one
of the three clearers in §1's L4, which weakens the precision argument — it does not break the
design.

---

## 10. Staleness and audit — the L3 implementation

No TTL, no lease, no background process (C8, SETTLED). Three signals, in descending order of
strength, all *reported*, never acted on automatically.

### 10.1 The verifier

```python
def git_branch_verifier(holder: str, holder_repo: str | None) -> str:
    """Return 'live' | 'gone' | 'unverifiable' for a branch-kind holder."""
    if not holder_repo:
        return "unverifiable"
    if db.git_rev_parse("HEAD", silent=True, cwd=holder_repo) is None:
        return "unverifiable"                       # repo unreachable / not a repo / no git
    if db.git_rev_parse(f"refs/heads/{holder}", silent=True, cwd=holder_repo) is None:
        return "gone"                               # repo IS reachable, branch is NOT there
    return "live"
```

**Zero new subprocess code.** `db.git_rev_parse` already exists at `db.py:210-226` and is already
used by `provenance.py` and `merge.py`. I verified its behaviour this run against this repo:
`HEAD` → `d171140338e6…`; `refs/heads/main` → same SHA; `refs/heads/no-such-branch-xyz` → `None`;
`cwd='/tmp'` (not a repo) → `None`.

**The two-call structure is the whole correctness of this function**, and it is why a one-liner would
be wrong: `git_rev_parse` returns `None` for *both* "branch absent" and "git unavailable". Probing
`HEAD` first separates a provable absence from an unknown, so an unreachable repo can never be
reported as an abandoned claim. That distinction is the difference between a gate you can trust and
another heuristic.

Argument-injection note: `holder` is interpolated into an argv element, never a shell string, and is
always prefixed with `refs/heads/`, so it cannot begin with `-` and cannot be read by git as an
option. Verified: `refs/heads/--upload-pack=evil` → `None`.

`audit()` takes `verifier` as a parameter, defaulting to `None`, in which case every claim reports
`liveness="unverified"` and nothing is pruned. The CLI passes `git_branch_verifier`; the MCP tool
passes it too; unit tests pass a stub. **`claims.py` itself never decides to run git.**

### 10.2 `audit()`

```python
def audit(conn, *, verifier=None, prune=False, actor="audit") -> dict[str, Any]
```

Per live claim it reports:

| field | meaning |
|---|---|
| `liveness` | `live` \| `gone` \| `unverifiable` \| `unverified` |
| `idle_seconds` | `now - renewed_at` — the weak signal, for non-branch holders |
| `divergent` | entity is terminal but the claim is still live → a clearer failed |
| `orphaned` | the entity row no longer exists |

`prune=True` closes **only** claims with `liveness == "gone"`, writing
`release_reason='audit:branch-gone'`, `released_by=actor`, and never restoring status:

```sql
UPDATE entity_claims
   SET released_at = ?, released_by = ?, release_reason = 'audit:branch-gone'
 WHERE claim_id IN (…) AND released_at IS NULL
RETURNING claim_id, entity_id, holder;
```

`cur.fetchall()` — never `rowcount` (§6.5).

`prune` is never automatic, never scheduled, and never touches `unverifiable` or merely-idle claims.
That is the user's "report, never auto-steal" constraint, honoured at the one place it would have
been tempting to bend.

### 10.3 What remains a guess, said plainly

For `holder_kind='agent'` there is no external referent and `idle_seconds` + a caller-chosen
threshold is all there is — **which is exactly today's precision, no better.** The design does not
pretend otherwise; it labels those claims `unverifiable` so a reader knows which signal they are
holding. The upgrade path is a better identity (a PID file, a session id, a lock file), not a better
threshold, and it needs no schema change: `holder_kind` already has a `CHECK` list to extend and a
`verifier` parameter to swap.

And the honest limit of the strong signal: **an existing branch proves unfinished work, not an
active agent.** For a gate whose false-refuse costs a `--allow-duplicate` and whose false-allow costs
40 minutes, erring toward "still held" is the right asymmetry — but it is an asymmetry, not a proof.

---

## 11. Milestone convergence — FATAL-1, both halves

### 11.1 `pull_next` — core layer, and the `held_by_other` policy the plan never stated

`pull_next` (`milestones/capacity.py:167-220`) runs its whole body inside `BEGIN IMMEDIATE` (`:182`).
Round 1 said "one line in an existing transaction" and would have called `claims.claim()`, entering a
second `BEGIN IMMEDIATE` → `OperationalError: cannot start a transaction within a transaction`,
executed by both adversaries and reconfirmed by me this run (P5).

**The fix is structural, not a flag.** `capacity.py` calls `claims._claim_core`, which by contract
opens no transaction and never commits (§4.2). Nesting is not avoided at runtime — it is never
attempted.

The insertion point is inside the existing candidate loop, which already has a "not eligible, keep
looking" branch:

```python
for item, milestone in _candidates(conn):
    if _eligibility_failure(conn, item, milestone, capacity, held) is not None:
        continue
    if item["item_kind"] in ("bug", "requirement"):
        res = claims._claim_core(
            conn,
            entity_id=item["item_ref"],          # 'CB-N' / 'FR-N' (milestones/_schema.py:55-57)
            holder=agent_id,
            holder_kind="agent",
            note=f"milestone pull {item['milestone_id']}",
        )
        if res["outcome"] in ("held_by_other", "entity_terminal"):
            continue                              # not eligible — try the next candidate
    chosen = item
    break
```

**The `held_by_other` policy, stated explicitly** (the plan never said this, and both adversaries
flagged the omission): **a held entity is an eligibility failure, not an error.** `pull_next` skips
it and keeps looking; if no candidate survives, it returns `None` exactly as it does today when
nothing is eligible. Nothing is rolled back because `_claim_core` performed no write on that path —
the upsert's `DO UPDATE … WHERE holder = excluded.holder` simply matched nothing. This composes with
`pull_next`'s existing contract instead of extending it.

`undetermined` cannot occur here: `_claim_core` does not classify contention. A `SQLITE_BUSY` inside
`pull_next` raises and hits the existing `except Exception: ROLLBACK; raise` at `:214-216` — which is
precisely how `pull_next` handles every other failure today. **Unchanged behaviour, deliberately.**

**One observable behaviour change, disclosed rather than buried.** `_claim_core` defaults to
`project=True`, so `pull_next` will now also set `findings.status='in_progress'`, which it does not
do today. I chose this over `project=False` because a claim that deliberately does not project
recreates exactly the ownership/status divergence this design exists to remove — and because
`milestone_items.status` is *already* set to `in_progress` on the same line (`capacity.py:196-201`),
so the finding lagging behind was arguably always the bug. Consequence: `query(status="in_progress")`
starts returning milestone-pulled findings. Criterion 6 ("existing consumers keep working") holds —
they see more rows, of the correct kind — but this belongs in the PR description and gets its own
test. If a reviewer disagrees, `project=False` is a one-word change with no other consequence.

Secondary effect, and a bug fix: `entity_terminal` means `pull_next` now refuses to pull an item
whose entity is already resolved. Today it will happily hand you a fixed bug.

### 11.2 `release_item` — the silent half

`release_item` (`capacity.py:223-275`) uses **no** `BEGIN IMMEDIATE` and commits at `:274` after
three writes. The adversary's smaller variant of X-1 lands here: a `release()` that commits partway
through would commit the item update without the capacity decrement, with **no exception raised**,
because the outer `commit()` at `:274` just becomes a second commit. Silent, and worse than the loud
`pull_next` failure.

Two changes:

1. `release_item`'s body is wrapped in `with db.txn(conn):` and its bare `conn.commit()` at `:274` is
   removed. **This is an atomicity bug fix independent of claims** — three writes that were never
   atomic become atomic.
2. `claims._release_core(conn, entity_id=item["item_ref"], holder=agent_id, restore_status=False,
   reason="milestone_release")` is added inside that block. `restore_status=False` because
   `release_item` is already setting the item's own terminal state; the finding's status is
   provenance's business, not the milestone's.

### 11.3 Convergence with `milestone_items.assigned_agent` — criterion 8, honestly bounded

1. **`entity_claims` becomes the source of truth for generic ownership.**
   `milestone_items.assigned_agent` (`milestones/_schema.py:64`) is redesignated a denormalized cache
   of the milestone-scoped view. No schema change, no migration, in this delivery.
2. **Both are written atomically** by §11.1 / §11.2.
3. **`item_kind='external'` is the honest gap.** External items carry no entity id, cannot be
   resolved by `EntityRef.of`, and keep `assigned_agent` alone. Stated, not papered over.
4. **A consistency test** asserts: for every `milestone_items` row with a non-null `assigned_agent`
   and a resolvable `item_ref`, `who_holds(item_ref)["holder"] == assigned_agent`. This is what turns
   "two representations" from a latent bug into a checked invariant.
5. **Out of scope, named:** later, `capacity.py` reads the holder from `entity_claims` and a
   migration drops `assigned_agent` / `pulled_at` / `idx_mi_assigned`.

**What I will not claim.** `pull_next`'s ownership is capacity-aware and size-gated
(`_eligibility_failure` at `:187`, `_upsert_capacity_increment` at `:202`). A generic claim carries
none of that. Full subsumption would mean absorbing capacity accounting into the claim store, which I
reject — that is milestone policy, not ownership. `assigned_agent` means "pulled under capacity"; a
claim means "held". Different predicates. This plan makes the *holder* agree and does not collapse
the semantics. Anyone claiming full subsumption is overselling.

---

## 12. `expected_status` + `changed` — and exactly how it relates to claims

This is the direct answer to the user's original sentence: *"узнать, был ли статус изменен, или это
был no-op"*. It is orthogonal to ownership and it is cheap.

### 12.1 The change

```python
def update_finding(conn, finding_id, *, status=None, expected_status=None, notes=None,
                   append_note=None, tags=None, meta_update=None, reported_at_ref=None) -> dict
def update_requirement(conn, req_id, *, status=None, expected_status=None, …) -> dict
```

- `expected_status` without `status` → `ValueError` ("expected_status requires status").
- Both are normalized through the existing resolvers (`types.resolve_finding_status`,
  `types.resolve_requirement_status`), so `--expected-status active` works like every other status
  input in the tool.
- The existing UPDATE at `findings.py:298` gains `AND status = ?` when `expected_status` is present:

```python
sql = f"UPDATE findings SET {', '.join(updates)} WHERE id = ?"
if expected_status is not None:
    sql += " AND status = ?"
    params.append(expected_status)
cur = conn.execute(sql, params)
changed = cur.rowcount == 1        # no RETURNING here — rowcount is the correct idiom (P9/P10)
```

- Response gains `"changed": bool`. Without `expected_status` it is trivially `True` (the row's
  existence was already checked at `findings.py:252`); with it, it is the compare-and-swap result.
- On `changed == False`, the response also carries `"expected_status"` and `"actual_status"`, and the
  row returned is the **current** row.
- **All-or-nothing, and this must be documented:** a CAS failure also discards `notes`, `tags`, and
  `meta_update` from the same call. That is the correct semantics and it is surprising, so it goes in
  the docstring and gets a test.

CLI: `--expected-status` on the `update` subparser (`findings.py:972-975`) and its `reqs` twin, plus
`--append-note` while we are there — `update_finding` has supported `append_note` since
`findings.py:241` and the CLI has never exposed it, which is why `worktree-setup.sh:204-205` says
*"No --notes: that field is a whole-value overwrite and would destroy the card's existing notes."*
**Three argparse lines remove a documented workaround from a second repository.**

Exit codes: `0` when `changed`, **`3`** when the CAS refused (parallel to `claim`'s `held_by_other`
— "the world was not as you expected"), `1` on error.

### 12.2 How the two mechanisms relate — no overlapping semantics

| | `expected_status` | claim |
|---|---|---|
| What it is | compare-and-swap on a **value** | a lease on an **entity** |
| Lifetime | one call | claim → release |
| Identity | none | `holder` + `holder_kind` + `holder_repo` |
| Storage | none | `entity_claims` |
| Question answered | "did my write change anything?" | "who is working on this?" |
| Failure means | someone else moved the value | someone else owns the work |

**Three rules that keep them from becoming two competing claim mechanisms:**

1. **`claim()` never accepts `expected_status`.** Two guards on one call would make the outcome
   vocabulary a cross product (`claimed`+`changed`, `claimed`+`unchanged`, …) for no gain. The
   claim's guard is *the holder*; that is the whole idea.
2. **`update_finding()` never reads `entity_claims`.** It does not refuse an update because someone
   else holds the entity. Ownership is advisory in this system — that is the user's model, and a
   status write that consulted claims would smuggle in enforcement nobody asked for.
3. **There is exactly one coupling, and it runs through `db.py`, not through either API:**
   `update_finding` / `update_requirement` fire the status-change hook, so moving a claimed entity to
   a terminal status auto-releases its claim (§9).

At the call site they compose cleanly and in order: **claim to acquire** (`worktree-setup.sh`, before
the irreversible act), **`--expected-status` to make later transitions detectable**
(`auto-resolve-codebugs.py`, which today cannot tell a real resolution from a re-run), **release at
finish**.

Also worth recording, though it is not mine to fix here: both `update_finding` and
`update_requirement` do a read-modify-write of the whole `meta` blob (`findings.py:252` read;
`:265`/`:271`/`:282` mutate copies), so two concurrent `meta_update` calls lose one. Architect A
found it, Opus and the Judge confirmed it, and I confirmed it again this run. It is a real shipped
bug in two modules, unrelated to whether any of this ships, and it should be filed.

---

## 13. Adoption — a first-class deliverable

### 13.0 First, what the shipped git guard already does — because Round 1 got this wrong

I opened `worktree-setup.sh:60-104` this run. The guard is **not** a worktree-path match. It parses
the card id out of the branch name and greps every branch in the repo:

```bash
CB_IDS=$(printf '%s' "${BRANCH_NAME}" | grep -oiE 'cb-?[0-9]{3,}' | … | sed -E 's/^cb-?/CB-/' | sort -u)
for cb in ${CB_IDS}; do
    others=$(git -C "${REPO_ROOT}" branch --format='%(refname:short)' \
             | grep -iE "cb-?${num}([^0-9]|$)" | grep -vx "${BRANCH_NAME}" || true)
    if [[ -n "${others}" ]]; then … exit 1 …
```

**So the CB-2534 case — two different slugs for one card, same repo — is already closed by shipped
code.** The path-match description in the Round-1 record is stale; that was the *pre*-CB-2489 guard.
Any adoption argument that claims the claim ledger prevents CB-2534 is claiming credit the git guard
has already earned, and I am not going to make it.

That leaves three things the git guard genuinely cannot do, and they are what the claim must be
justified by:

1. **It only sees branches.** An agent working in the main checkout, or in a worktree created by
   hand, or by any path other than this script, leaves no branch matching the pattern and trips
   nothing. The tracker is the only shared place.
2. **It only runs in this one script.** The `fix-latest-codebugs` skill, a human, an MCP client, and
   `pull_next` all bypass it entirely.
3. **It has the same precision problem, one layer up — and this is the strongest argument.** A
   branch that was merged and never deleted trips the guard forever. The guard's own failure mode is
   therefore identical to `in_progress`'s: accumulating false positives that train people to pass
   `--allow-duplicate` by reflex, which is exactly what the `:118-121` comment predicts about the
   tracker. **A claim record that is released at merge time is the one thing that can tell a stale
   branch from work in flight.** That is not a nicer version of the guard; it is the missing input
   the guard needs to stay trustworthy.

The diff below is therefore designed to **refine the guard, not to sit beside it.**

### 13.1 The CLI surface

`claims.py` provides `register_cli(sub, commands)` and calls `db.register_cli_provider("claims",
register_cli)` at module level, per CLAUDE.md.

```
codebugs claim <ID>   --holder H [--holder-kind branch|agent|human|process] [--repo PATH]
                      [--note TEXT] [--no-project] [--allow-terminal] [--json]
codebugs release <ID> --holder H [--no-restore] [--reason TEXT] [--json]
codebugs steal <ID>   --holder H --expect OLD --reason TEXT [--json]
codebugs who-holds <ID> [--quiet] [--json]
codebugs claims       [--holder H] [--kind K] [--holder-kind K] [--divergent]
                      [--stale-after SEC] [--all] [--json]
codebugs claims-audit [--prune] [--json]
codebugs claim-history <ID> [--json]
```

**Exit codes are the API for shell callers** — this is what makes the surface usable from a
`set -euo pipefail` script without parsing:

| code | `claim` | `who-holds` | `update --expected-status` |
|---|---|---|---|
| 0 | `claimed` or `already_mine` → proceed | held (prints holder) | `changed=true` |
| 1 | error (bad id, no such entity) | error | error |
| 3 | `held_by_other` → refuse | **not held, but claim history exists** | CAS refused |
| 4 | `entity_terminal` → refuse | **no claim record ever for this id** | — |
| 5 | `undetermined` → retry | — | — |

`who-holds`'s split between 3 and 4 is load-bearing for §13.2 and is the reason it is a separate verb
rather than a flag on `claims`: the shell must distinguish "released, therefore the branch is
leftovers" from "this card predates the mechanism, assume nothing".

Human output is one stable line on stdout; `--json` emits the full response dict. This matches the
repo's existing convention — only `_cmd_get` prints JSON today (`findings.py:815`), everything else
prints text — so `--json` is the new part and it is opt-in.

### 13.2 The exact diff — `/home/faxik/w/autosorter/tools/worktree-setup.sh`

**(a) Refine the git guard — insert before `if [[ -n "${others}" ]]` at `:86`:**

```bash
    # A branch that exists but carries no LIVE claim was released at merge time
    # (worktree-finish.sh). That is leftover git, not work in flight — and
    # refusing on it is the false positive that trains people to pass
    # --allow-duplicate by reflex. Only downgrade when the card is KNOWN to the
    # claim ledger (exit 3); exit 4 means "never claimed", where we assume nothing.
    if [[ -n "${others}" ]] && command -v codebugs >/dev/null 2>&1; then
        codebugs who-holds "${cb}" --quiet >/dev/null 2>&1
        case $? in
            3) echo "note: ${cb} — branches exist but the claim was released; treating as leftovers:"
               echo "${others}" | sed 's/^/    /'
               others="" ;;
        esac
    fi
```

**(b) Replace the whole `:105-133` "registry check + claim" block.** It becomes a real gate, and it
runs **before** `git worktree add` at `:141`:

```bash
    # Claim the card. This is a GATE, not a note: it runs before the
    # irreversible act, and a refusal aborts. AUTOSORTER_SETUP_NO_CLAIM lets
    # tests exercise the guard without writing to the real findings database.
    if command -v codebugs >/dev/null 2>&1 && [[ -z "${AUTOSORTER_SETUP_NO_CLAIM:-}" ]]; then
        set +e
        codebugs claim "${cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
                 --repo "${REPO_ROOT}" --note "worktree-setup ${SLUG}"
        _rc=$?
        set -e
        case "${_rc}" in
            0) _claim_ids="${_claim_ids} ${cb}" ;;
            3) echo ""
               echo "  Verify the holder is real:  git -C '${REPO_ROOT}' rev-parse --verify refs/heads/<holder>"
               echo "  If that branch is gone:     codebugs claims-audit --prune"
               echo "  To take it deliberately:    codebugs steal ${cb} --holder ${BRANCH_NAME} --expect <holder> --reason '...'"
               echo "  To proceed anyway:          re-run with --allow-duplicate"
               [[ "${ALLOW_DUPLICATE}" == "1" ]] || exit 1 ;;
            4) echo "  ${cb} is already resolved; a follow-up branch must not reopen it."
               [[ "${ALLOW_DUPLICATE}" == "1" ]] || exit 1 ;;
            5) sleep 1
               if codebugs claim "${cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
                          --repo "${REPO_ROOT}" --note "worktree-setup ${SLUG}"; then
                   _claim_ids="${_claim_ids} ${cb}"
               else
                   echo "  ⚠ ${cb}: codebugs stayed busy; continuing UNCLAIMED."
               fi ;;
            *) echo "  note: ${cb} could not be claimed (codebugs error); continuing unclaimed." ;;
        esac
    fi
```

**(c) Release the claim if setup aborts after claiming** — new, immediately after the loop that
ends at `:134`:

```bash
_release_claims_on_abort() {
    for cb in ${_claim_ids}; do
        codebugs release "${cb}" --holder "${BRANCH_NAME}" \
                 --reason "worktree-setup aborted" >/dev/null 2>&1 || true
    done
}
trap '_release_claims_on_abort' ERR
```

and `trap - ERR` just before the script's final success banner (`:259` region). **Without this, a
setup that dies between the claim and `git worktree add` leaks a claim — and a leaked claim is
exactly the pathology this design exists to remove.** This is the piece Round 1 did not have, and it
is not optional.

**(d) Delete `:206-212`.** The `for cb in ${_claim_ids}; do codebugs update … --status in_progress`
loop is now redundant: the claim at (b) already projected `findings.status='in_progress'` through
`EntityKind.busy_status` (§8), inside the same transaction as the claim. Removing it deletes the
last write-only status write in the script.

**(e) Delete the `codebugs get | sed` pre-read at `:110-111`.** Its job — "only `open` cards are
flipped, a follow-up branch on a `fixed` card must not silently reopen it" (`:203-205`) — is now
`claim`'s `entity_terminal` outcome, decided inside a transaction instead of in a shell
check-then-act race.

**Net: +38 / −27 lines. Two shell races removed** (the `get`-then-`update` read-then-write, and the
guard's unconditional refusal), **one added obligation** (the `ERR` trap).

### 13.3 `worktree-finish.sh` — the release, and the honest gap

`worktree-finish.sh` calls **no** `codebugs` binary directly; it shells `auto-resolve-codebugs.py`
(`:1120`) and `auto-mark-milestone-integration.py` (`:1133`). So there is no existing hook to attach
to and one must be added, in the same guarded style:

```bash
# [7d/9] Release claims held by this branch. Idempotent; safe if never claimed.
if [[ "${SKIP_CHECKS}" != true ]] && command -v codebugs >/dev/null 2>&1; then
    codebugs claims --holder "${BRANCH_NAME}" --json 2>/dev/null \
      | grep -oE '"entity_id": *"[^"]+"' | sed -E 's/.*"([^"]+)"$/\1/' \
      | while read -r cb; do
            codebugs release "${cb}" --holder "${BRANCH_NAME}" --no-restore \
                     --reason "branch merged" >/dev/null 2>&1 || true
        done
fi
```

**Two honest notes.** First, ordering: this must run **after** `auto-resolve-codebugs.py` at `:1120`,
because that script moves findings to `fixed`, which fires the status-change hook and auto-releases
the claim already (§9). By the time this block runs it is usually a no-op — and that is the point:
it is the belt for the cases the trailer did not cover (a branch merged without a `Fixes:` trailer,
a card worked but not resolved). Second, it grep-parses JSON, which is the same fragility as
`:110-111` today; a `--format=ids` flag on `codebugs claims` would remove it and is worth adding if
this survives review.

### 13.4 What the claim can and cannot accomplish, by position — asked directly, answered directly

| Position | What a `held_by_other` can accomplish |
|---|---|
| **Where the claim is today (`:206`, 65 lines AFTER `git worktree add`)** | **Nothing.** The worktree exists, the branch exists, the agent is about to start. The outcome selects an `echo`. This is why Round 1's adoption plan, which pointed at this line, was worth less than it looked. |
| **Where this design puts it (`:105-133`, BEFORE `:141`)** | A real refusal. Nothing irreversible has happened; `exit 1` costs the user a re-run with `--allow-duplicate`. |
| **Inside `pull_next` (§11.1)** | A real refusal, and free — the candidate is skipped and the loop continues. |
| **In `fix-latest-codebugs/SKILL.md`** | Advisory only, and outside the repo. Worth fixing (the documented call `mcp__codebugs__update(id=…, assignee=…)` cannot succeed — `findings.py:573-581` has neither parameter) but **it does not count as adoption** and I am not counting it. |

**So yes: the claim must move earlier, and moving it is the deliverable.** The cost of moving it is
the `ERR` trap in (c) — a claim taken before the irreversible act must be given back if the
irreversible act never happens. That is a real new obligation on the script and I am not pretending
it is free.

### 13.5 In-repo adoption, so a clone gets something

Items (a)–(e) live in a second repository, which is exactly the criticism Codex levelled at every
Round-1 proposal. Three things ship **inside** `codebugs` so a fresh clone is not left with an
optional tool nobody calls:

1. **`pull_next` and `release_item` call the core layer** (§11). In-repo, tested, no shell involved.
2. **`codebugs get <ID>` gains a `claim` block** in its response — 4 lines in `findings.get_finding`,
   calling `claims.who_holds`. Ownership becomes visible in the place people already look, which is
   the cheapest possible discoverability.
3. **`codebugs summary` gains one line**: `Claims: N held, M divergent, K idle >24h`. That is the
   §1-L5 health check, surfaced where someone will actually see it decay.

### 13.6 Definition of done for adoption

The feature is **not delivered** — regardless of how complete `claims.py` is — until all four hold:

- a test asserts two `claim()` calls with different holders yield `claimed` then `held_by_other`,
  over two connections to a file DB;
- a test asserts `pull_next` skips an entity held by another agent and pulls the next candidate;
- `worktree-setup.sh` items (a)–(e) are merged in `/home/faxik/w/autosorter`, with its existing
  guard test extended to cover a `held_by_other` refusal (the script already supports
  `AUTOSORTER_SETUP_NO_CLAIM` for exactly this);
- `worktree-finish.sh` §13.3 is merged, and a manual end-to-end run of setup → finish leaves
  `SELECT count(*) FROM entity_claims WHERE released_at IS NULL` at zero.

That last assertion is the whole design in one query. If it does not hold, §1's L4 has already failed
and the record is on its way to being the 42nd stale row.

---

## 14. Migration and back-compat

**Schema.** `entity_claims` is a new table created by `CREATE TABLE IF NOT EXISTS` inside
`claims.ensure_schema`, registered via `db.register_schema("claims", ensure_schema)`. Additive by
construction. No existing table is altered by this design — not `findings`, not `requirements`, not
`milestone_items`, not `reqs`'s CHECK constraint.

**`EntityKind.busy_status`** is a trailing field with a `None` default on a frozen dataclass, so
every existing `EntityKind(...)` construction and every unpacking of `ENTITY_KINDS` keeps working
unchanged. `tests/test_entities.py` needs no edit.

**API back-compat.**

- `update_finding` / `update_requirement`: `expected_status` is a new keyword-only parameter
  defaulting to `None`. With it omitted, behaviour is byte-identical to today except that the
  response dict gains a `"changed": true` key. Adding a key to a returned dict is the same
  compatibility class as every prior response change in this repo.
- MCP `update` gains `expected_status`. Existing clients that do not pass it see no change.
- CLI `update` gains `--expected-status` and `--append-note`. Existing invocations are unaffected.
- **One deliberate behaviour change**: `pull_next` now also projects `findings.status='in_progress'`
  and refuses terminal entities (§11.1). Disclosed, tested, and reversible with `project=False`.

**Wiring, per CLAUDE.md's "Current rules for new code":**

- `db.py:487` — add `claims` to the `_ensure_modules_loaded` import list. It must come **after**
  `findings` and `reqs` in that list is irrelevant (registration order does not matter for hooks),
  but `claims` must be imported for its `register_status_change_hook` call to run at all.
- `server.py:22-32` — `SERVER_NAMES["claims"] = "codeclaims"`.
- `cli.py:49` — add `"claims"` to the `--mode` choices.

**No data migration. No backfill of the ~41 existing `in_progress` findings.** This is deliberate and
it is §1-L5: the table starts empty *on purpose*. Backfilling would import exactly the garbage that
makes the current signal ungateable, with invented holders and invented claim times. The existing 41
remain a `findings.status` problem, visible via `codebugs query --status in_progress`, and they are
someone's cleanup task — not this table's contents.

**Rollback.** If the design is reverted, `DROP TABLE entity_claims` plus reverting the code leaves
`findings` and `requirements` exactly as they were, because the only thing claims ever wrote into
them is a status value they were always allowed to hold.

**CLAUDE.md amendments required by this design** (each is a rule that is otherwise undiscoverable):

1. `entities.py` is the sanctioned cross-table status **write** as well as read; `EntityRef.set_status`
   is the only such write (§2.1).
2. The `RETURNING` rule: a statement reads its outcome by fetching **or** from `rowcount`, never both
   (§6.5).
3. Never write a plain `BEGIN`; `db.txn` is the only place `BEGIN IMMEDIATE` appears (§4.1).
4. A `claims` section describing the module, alongside the existing `Milestones module` section.
5. **Stale-fact correction, unrelated but found in passing:** the `blockers.py` debt bullet cites
   `db._row_to_dict()` / `reqs._row_to_dict()`, neither of which exists — it is public
   `db.row_to_dict` at `db.py:229-240`.

---

## 15. Testing strategy

`tests/test_claims.py`, own fixtures, no shared `conftest.py`. Concurrency tests follow the two
existing precedents, which I read this run:

- `tests/test_milestones.py:801-846` — `test_two_threads_two_connections_no_double_claim`: two
  threads, each `db.connect(tmp_project)` (the **production** discovery path), `threading.Barrier(2)`,
  asserts uniqueness of claimed refs.
- `tests/test_sweep.py:754-799` — `test_concurrent_upsert_atomic`: 10 threads, raw
  `sqlite3.connect(db_path, timeout=10.0)`, `threading.Barrier(N)`, asserts one row with
  `recurrence_count == N`.

I follow the **milestones** shape (production `db.connect`) for the headline race and borrow the
sweep test's N-thread stress for the retry test.

### 15.1 Tests that prove the success criteria

| # | Test | Proves |
|---|---|---|
| 1 | 2 threads / 2 connections / barrier → exactly one `claimed`; **the loser's outcome string is `held_by_other` and its response names the winner** | criterion 1. The existing precedents assert uniqueness only; asserting the loser's *report* is the entire point of this feature |
| 2 | `utc_now` monkeypatched to a constant; two `claim()` calls, same holder → `claimed` then `already_mine` | criterion 2. **Fails on any timestamp-based discriminator**; passes on `touch_count` |
| 3 | 10 threads all claiming as the *same* holder → 1 `claimed` + 9 `already_mine`, `touch_count == 10` | criterion 2 under load |
| 4 | `who_holds` / `held_by` return the right rows; `EXPLAIN QUERY PLAN` shows index use on both | criterion 3, and that it stays a point query |
| 5 | A **synthetic third `EntityKind`** declared in the test with `busy_status='working'` claims, projects, and releases with **zero changes to `claims.py`** | criterion 4, executed rather than asserted in prose |
| 6 | Full claim → release lifecycle on `FR-1`; `projected == false`, `projection == "not_declared"`, no CHECK violation | criterion 5 |
| 7 | `query(status="in_progress")` returns claimed findings | criterion 6 |
| 8 | Stub verifier returns `gone` → `audit()` reports it; `audit(prune=True)` closes it with `release_reason='audit:branch-gone'`; `unverifiable` and merely-idle claims are **untouched** | criterion 7 |
| 9 | Consistency invariant: every `milestone_items` row with `assigned_agent` + resolvable `item_ref` has `who_holds(item_ref)["holder"] == assigned_agent` | criterion 8 |
| 10 | `pull_next` skips a held entity and pulls the next candidate; `tests/test_milestones.py:801` still passes unmodified | criterion 9, and no regression |

### 15.2 Tests that exist because a specific defect was found

| # | Test | Defect it locks down |
|---|---|---|
| 11 | Open a transaction, call every `_*_core`, assert `conn.in_transaction` is still `True` | FATAL-1 — a core function that commits fails here |
| 12 | Nested `with db.txn(conn): with db.txn(conn):` — inner yields `False`, no `OperationalError`, exactly one commit | FATAL-1 |
| 13 | `steal` returns `stolen` **and** a follow-up `who_holds` shows the new holder | FATAL-2 — the `rowcount`-on-`RETURNING` trap; the R1 implementation would fail this while having written |
| 14 | Force a failure inside `db.txn` after SQLite auto-rolled back; assert the **original** exception surfaces, not `cannot rollback` | SERIOUS-3 |
| 15 | `busy_timeout=0` + a held write lock → `claim()` returns `outcome="undetermined"`, no exception escapes | C4 / the fourth outcome. Without this test the fourth outcome is documentation |
| 16 | Inject a non-contention `OperationalError` (`sqlite_errorcode=1`) → it **propagates**, is not reported as `undetermined` | SERIOUS-3, the other direction |
| 17 | Claim `FR-1`, then `update_requirement(status="implemented")` → claim auto-released, `release_reason='terminal:implemented'` | SERIOUS-5 |
| 18 | Claim CB-1, `update_finding(status="fixed")` → auto-released, and the row is **still there** with `released_at` set and `release_reason='terminal:fixed'` | SERIOUS-7 — the audit fact now has a home |
| 19 | Spy hook counts invocations: a claim followed by a release fires **zero** status-change hooks | §8.2's no-recursion invariant |
| 20 | Claim (projects `open→in_progress`) → steal by B → release by B → status restored to **`open`**, not `in_progress` | §6.3's `prev_status` carry-forward |
| 21 | Claim → holder sets `fixed` → `release()` → status stays `fixed`, `status_restored == false` | Q5 — release never resurrects |
| 22 | `expected_status` mismatch → `changed == false`, `actual_status` reported, **and the `notes` in the same call are not applied** | §12.1's all-or-nothing semantics |
| 23 | `git_branch_verifier` against a real `git init` tmp repo: existing branch → `live`; deleted branch → `gone`; non-repo cwd → `unverifiable` | §10.1 — the two-call structure |
| 24 | Source-tree invariant: no plain `BEGIN` outside `db.txn` (grep `src/` for `BEGIN` not followed by `IMMEDIATE`) | §4.1's rule, ratcheted |
| 25 | CLI exit codes via `subprocess`: `claim` twice with different holders → `0` then `3`; on a fixed finding → `4` | §13.1 — the shell contract is the API |

Test 25 matters more than its size suggests: **the exit codes are what `worktree-setup.sh` gates on**,
so an unasserted exit code is an unasserted gate.

---

## 16. Effort — split honestly

Round 1 said **M**, and priced adoption at "three one-line wiring edits plus a `SKILL.md` line". The
adversary called that understated for delivery and was right. Revised:

### Module — **M/L**

| Component | Lines | Notes |
|---|---|---|
| `claims.py` | ~330 | schema, 3 cores, 3 public writes, 4 reads, audit, verifier, 8 MCP tools, 7 CLI verbs |
| `db.py` | ~70 | `txn` (~35), hook pair (~30), `busy_timeout` (1) |
| `entities.py` | ~21 | `busy_status` field (1), `set_status` (~20) |
| `findings.py` | ~34 | hook fire (6), `expected_status` (15), CLI flags (6), `get` claim block (4), summary line (3) |
| `reqs.py` | ~20 | hook fire (6), `expected_status` (14) |
| `milestones/capacity.py` | ~35 | `pull_next` (12), `release_item` (8), `db.txn` refactor (15, mechanical) |
| `merge.py` | ~15 | `db.txn` refactor, mechanical, behaviour-identical |
| `server.py` / `cli.py` | 2 | one line each |
| `tests/test_claims.py` | ~400 | the 25 tests above |
| edits to existing tests | ~120 | milestones, findings, reqs |
| **Total** | **~1050** | of which ~520 is test code |

Round 1 said "~200 lines including tools and CLI" for `claims.py`. That was optimistic before the
soft-delete lifecycle, the audit/verifier layer, and the core/wrapper split existed. ~330 is the
honest number and I would not be shocked by 380.

### Delivery — **L**, and this is where the risk lives

| Component | Effort | Why it is not small |
|---|---|---|
| `worktree-setup.sh` (a)–(e) | ~half a day | A second repository. `set -euo pipefail` + an `ERR` trap + a new exit-code contract, in a script whose every existing `codebugs` call is deliberately best-effort. The convention being broken is the feature, and breaking it wrongly breaks worktree creation for everyone. |
| its test | ~2 h | The script already supports `AUTOSORTER_SETUP_NO_CLAIM`; the existing guard test extends to a `held_by_other` refusal. |
| `worktree-finish.sh` §13.3 | ~2 h | New block, no existing hook, ordering constraint relative to `:1120`. |
| manual end-to-end | ~1 h | setup → work → finish, then assert zero live claims. Cannot be automated across two repos. |
| CLAUDE.md amendments | ~1 h | Five items in §14. |

**Total: 2–3 focused days for one implementer**, front-loaded on the module and back-loaded on the
shell. Sequence: `busy_timeout` commit → `db.txn` + refactor `merge.py`/`capacity.py` (behaviour-neutral,
lands alone) → `entities.set_status` + `busy_status` → `claims.py` + tests → hooks in `findings`/`reqs`
→ `expected_status` → milestone wiring → the two shell diffs. Each of the first three lands
independently and is useful on its own, which is deliberate: if this stalls halfway, what shipped is
still a net improvement.

---

## 17. Defect ledger — every mandatory fix, and where it is fixed

| # | Defect (Round 1) | Status | Where |
|---|---|---|---|
| **FATAL-1** | `claims.claim()` inside `pull_next`'s open transaction → `cannot start a transaction within a transaction`; and `held_by_other` policy unstated | **FIXED, structurally** | §4.2 core/wrapper split — `capacity.py` calls `_claim_core`, which opens no transaction. Nesting is never attempted, and `db.txn` is reentrant as a second line (§4.1). `held_by_other` = eligibility failure → `continue` (§11.1). `release_item`'s silent half fixed in §11.2. Test 11, 12 |
| **FATAL-2** | `steal` reads `rowcount` on a `RETURNING` statement → always reports `held_by_other` while having written | **FIXED** | §6.3 fetches. §6.5 audits all 8 statements in the design against this trap and states the rule for CLAUDE.md. Also found and fixed a second `steal` defect nobody raised: `prev_status` must be carried forward from the victim or release restores to the wrong status. Test 13, 20 |
| **SERIOUS-3** | `undetermined` masked by its own handler — `ROLLBACK` in `except` raises a new `SQLITE_ERROR` that the string-matching guard re-raises | **FIXED at the root** | §4.1 guards the rollback with `conn.in_transaction` and swallows its `OperationalError` so cleanup can never replace the real exception. §5.2 classifies on `exc.sqlite_errorcode`, not `str(exc)` — verified P5/P6/P7. Test 14, 15, 16 |
| **SERIOUS-4** | `register_post_update_hook` is new `db.py` infrastructure with one consumer | **JUSTIFIED, and strengthened** | §9.3, five arguments: it is the only mechanism reaching the live call site; it is the missing symmetric half of `register_post_add_hook` (`db.py:178-204`), not new infrastructure; it now has two producers; it is the only correct C1 answer; the alternative was rejected on layering, not effort. Disclosed fallback: read-time `divergent` reporting |
| **SERIOUS-5** | Terminal hook findings-only; requirements update through `reqs.py:163` | **FIXED** | §9.1 fires from both `findings.update_finding` (`:298`/`:299`) and `reqs.update_requirement` (`:222`/`:223`). Test 17 |
| **SERIOUS-6** | B1→B3 is not an additive upgrade: `entity_id` PK vs `claim_id` PK requires a table rebuild | **RETRACTED and REDESIGNED** | §3.1. `claim_id TEXT PRIMARY KEY` + `CREATE UNIQUE INDEX … WHERE released_at IS NULL` gives identical exclusion (verified P2/P3) while allowing unlimited closed rows. The upgrade is not needed because history is present from day one |
| **SERIOUS-7** | Promises to "record an auto-release" with nowhere to store it | **FIXED** | §3.1 soft delete: `released_at` / `released_by` / `release_reason`. `release_reason='terminal:fixed'` is a queryable record, and the `GROUP BY release_reason` query is the health check for §1-L4. Test 18 |
| **MEDIUM-8** | "Third kind by declaration only" not met — each projecting domain must implement a callback | **FIXED** | §8.1. Projection is `EntityKind.busy_status`, one optional declarative field. The projector registry is deleted. Test 5 proves it with a synthetic third kind and zero `claims.py` changes |

### 17.1 Round-1 claims I am retracting

- *"`entity_claims` is a strict column-subset of Solution 3's table; the upgrade is additive."*
  **False** (SERIOUS-6). Retracted and designed around.
- *"Idempotent by name, matching `db.register_post_add_hook` discipline."* — `register_schema`
  **raises** on duplicate (`db.py:49-64`); the post-add hook silently returns. I repeated a false
  characterization from the hook's own docstring.
- *"`_SAFE_IDENT` is the guard on interpolated identifiers."* It is **defined and never referenced**
  (`entities.py:20`); the real guard is `readable_cols` membership at `entities.py:83-84` plus the
  frozen `ENTITY_KINDS`.
- *"`db.immediate_txn`… `capacity.py:179-218` is refactored onto it"* — written as if the helper
  existed. It does not; `db.py` contains no `BEGIN` at all. It is new code in this design.
- *"Adoption is three one-line wiring edits plus a SKILL.md line."* Understated by roughly a day
  (§16). The `SKILL.md` line is outside the repo and does not count.
- *"The claim at `worktree-setup.sh:208` is the live claim site to attach to."* It is — but it runs
  **after** the irreversible act, so attaching there accomplishes nothing (§13.4). The claim has to
  move.
- *The implicit assumption that the shipped git guard matches worktree paths.* It parses the card id
  out of the branch name and greps all branches (`:74-84`), so the CB-2534 class is **already
  closed** by shipped code (§13.0). The claim ledger must be justified on what the guard cannot do.

---

## 18. Risks, in order, and what I would cut first

**Risk 1 — the clearers stop working, and `entity_claims` becomes the new stale pile.** This is
larger than every technical defect in §17 combined. §1-L5's whole argument is that the table starts
empty and stays honest; it stays honest only while the terminal hook, the `worktree-finish` release,
and the `ERR` trap all work. Mitigation: `divergent` is computed on every read (§6.4),
`claims --divergent` enumerates failures, `codebugs summary` shows the count (§13.5), and §13.6's
definition of done includes "zero live claims after a full setup→finish cycle". **If that number
starts climbing, this design has failed and should be reverted, not patched.** I would rather write
the falsification condition down now than defend it later.

**Risk 2 — the shell diff does not land.** Then the claim is advisory, `pull_next` is the only real
consumer, and the honest verdict is that the module was built for one in-repo caller. Mitigation:
§13.5 ships three in-repo consumers so a clone is not left with an unused tool. But I will not
pretend that substitutes for §13.2.

**Risk 3 — the `db.py` hook seam is rejected in review.** Degrades to read-time divergence reporting:
visible and manually recoverable, not silently wrong. Costs one of three clearers, which feeds
Risk 1.

**Risk 4 — `pull_next`'s new status projection surprises a consumer.** Disclosed in §11.1, one-word
reversal.

**Risk 5 — two ownership representations during convergence.** Mitigated to a checked invariant by
test 9, not to zero.

### What I would cut if the budget halved

**Cut:** `steal` (its job is mostly covered by `claims-audit --prune` followed by a normal claim);
the `history` MCP tool (the CLI verb is enough); the `summary` line; `holder_kind='process'`.

**Would not cut, at any budget:** the `ERR` trap in §13.2(c); the terminal-status hook; the
core/wrapper split; `who-holds`'s 3-vs-4 exit-code split; and test 1's assertion on the *loser's*
outcome string. Each of those is load-bearing for either correctness or trustworthiness, and each is
small.

### The single thing I am least sure of

Whether `holder_kind='branch'` is the right identity for the *general* case, or only for the one call
site that has a branch in scope. Inside `pull_next` the holder is an `agent_id` with no external
referent, so it lands in the `unverifiable` bucket and gets today's precision. If most claims end up
being `agent`-kind, L3 covers a minority of the table and the trustworthiness argument is thinner
than §1 makes it sound. The schema is designed so the answer is swappable — `holder_kind` has a
`CHECK` list to extend and `audit()` takes the verifier as a parameter — but I would rather flag that
the strong signal may cover less ground than the argument implies than discover it after shipping.
