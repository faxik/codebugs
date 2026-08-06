# Architect B — Dedicated Store / Capability First

**Perspective:** ownership is a concept with its own lifecycle. It deserves its own home rather
than being smeared across whichever domain table needed it first.

**Forbidden lever (hard):** I may not add or alter any column on `findings`, `requirements`, or
`milestone_items`. I may write legal values into their existing `status` columns.

---

## 0. Verification ledger — every line I cite, I opened this run

| Citation | What is actually there |
|---|---|
| `src/codebugs/entities.py:1-114` | Whole file. `_SAFE_IDENT` at `:20`, `EntityKind` frozen dataclass `:23-33`, `ENTITY_KINDS` `:36-55`, `EntityRef.of` `:72-78`, `_read` `:80-89`, `require` `:105-108`, `field` `:110-113` |
| `src/codebugs/entities.py:20` vs `tests/test_entities.py:147-152` | **`_SAFE_IDENT` is never used at runtime.** Its only consumers are three asserts in `test_all_registry_identifiers_are_safe`. Runtime protection in `_read` comes from the `readable_cols` membership check at `entities.py:83-84`, not from the regex. |
| `src/codebugs/blockers.py:430-440` | `query_deferred_entities` calls `entity_kind()` and interpolates `kind.table` / `kind.sort_col` into SQL **outside `entities.py`**, annotated `# noqa: S608 (identifiers from frozen registry)` at `:437`. |
| `src/codebugs/findings.py:15-37` | `SCHEMA`; `status` CHECK at `:21-22` includes `'in_progress'`; `idx_findings_status` at `:33` |
| `src/codebugs/findings.py:51-97` | `_migrate_statuses` — a **full table rebuild** (create `findings_new`, copy, drop, rename, recreate 5 indexes) purely to widen a `status` CHECK |
| `src/codebugs/findings.py:235-245` | `update_finding` signature (kw-only after `finding_id`) |
| `src/codebugs/findings.py:298` | `conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)` — the single choke point, no rowcount check |
| `src/codebugs/findings.py:573-581` | MCP `update(finding_id, status, notes, tags, meta_update, reported_at_ref)`. **No `id`, no `assignee`.** |
| `src/codebugs/findings.py:605-609` | The tool already calls `entities.EntityRef.of(finding_id).is_resolved(conn)` post-update — precedent for the tool doing entity-layer work |
| `src/codebugs/reqs.py:15-36` | `REQS_SCHEMA`; `status` CHECK at `:22-23` = `planned, partial, implemented, verified, superseded, obsolete`. No `in_progress`, no owner column. |
| `src/codebugs/reqs.py:163-175`, `:222` | `update_requirement` + its UPDATE |
| `src/codebugs/reqs.py:624-635` | MCP `reqs_update` |
| `src/codebugs/types.py:12-14` | `utc_now()` → `strftime("%Y-%m-%dT%H:%M:%SZ")` — **second resolution** |
| `src/codebugs/types.py:21`, `:36`, `:39`, `:41` | `FINDING_STATUSES`, `FINDING_TERMINAL`, `REQUIREMENT_STATUSES`, `REQUIREMENT_TERMINAL` |
| `src/codebugs/provenance.py:261-271` | Skips when `current["status"] in types.FINDING_TERMINAL` (`:261`), else calls `findings.update_finding(..., status=status_input, append_note=...)` at `:265-270` |
| `src/codebugs/db.py:178-190` | `register_post_add_hook(name, fn)` — name-keyed, idempotent on re-import |
| `src/codebugs/db.py:193-204` | `run_post_add_hooks` — **swallows every exception** and writes to stderr (`:203-204`) |
| `src/codebugs/db.py:478-489` | `_ensure_modules_loaded` imports `findings, provenance, reqs, merge, sweep, bench, blockers, milestones` at `:487` |
| `src/codebugs/db.py:492-503` | `connect()` — sets only `journal_mode=WAL` at `:497`; runs every module's `ensure_fn` at `:500-501` |
| `src/codebugs/server.py:22-32` | `SERVER_NAMES` dict, 9 keys |
| `src/codebugs/cli.py:49` | `choices=["findings","provenance","reqs","merge","sweep","bench","blockers","milestones","all"]` |
| `src/codebugs/milestones/_schema.py:52-77` | `milestone_items`; `assigned_agent` `:64`, `pulled_at` `:65`, `UNIQUE(milestone_id,item_kind,item_ref)` `:72`, partial `idx_mi_assigned` `:77` |
| `src/codebugs/milestones/capacity.py:179-218` | The isolation save/restore + `BEGIN IMMEDIATE` (`:182`) + guarded UPDATE (`:196-201`) + `finally` restore (`:217-218`) |
| `~/.claude/skills/fix-latest-codebugs/SKILL.md:92` | `mcp__codebugs__update(id="CB-1234", status="in_progress", assignee="claude")` — **two of three kwargs do not exist** in the signature at `findings.py:574-581` |

Two facts from that ledger reshape my whole proposal and I want them stated before the designs:

**(i) The "identifier allowlist" is a test invariant, not a runtime guard.** The brief describes
`entities.py` as owning `_SAFE_IDENT`. It owns the constant, but nothing at runtime calls it —
`tests/test_entities.py:147-152` is the only consumer. The *runtime* guard on `_read` is the
`readable_cols` set membership at `entities.py:83-84`. So "does my write duplicate the identifier
allowlist" is a question about **where interpolated identifiers come from**, not about reusing a
regex.

**(ii) The repo already sanctions interpolating `EntityKind` identifiers outside `entities.py`.**
`blockers.py:437-438` does exactly that, with an explicit `noqa` justification. So a claims module
doing the same would not be a new precedent. I still refuse to do it (see Q2 below) — but for
design reasons, not safety ones.

---

## 1. Does my forbidden lever make the problem unsolvable?

No — but it bites once, sharply and usefully, and I want that on the record because it is a real
finding rather than a complaint.

The user's constraint list says the claim **"projects into the entity's status."** For findings
that costs nothing: `'in_progress'` is already a legal value (`findings.py:21-22`), so writing it
is a value write, not a schema change. For requirements it is impossible under my lever:
`REQUIREMENT_STATUSES` (`types.py:39`) has no busy state, and adding one means rewriting the
column's CHECK constraint — which, in this codebase, means the 45-line table rebuild at
`findings.py:51-97`. That is altering a column on `requirements`. Forbidden to me.

**I claim the constraint is exposing a defect in the requirement, not in the design.** "Project
into the entity's status" is a *per-kind* assumption wearing the clothes of a generic capability.
Success criterion 4 says adding a third entity kind must need no new ownership code; if every kind
must first grow a busy status, then criterion 4 is false by construction — kind #3 needs a
migration before it can be claimed. The two criteria are in tension and my lever forces me to
resolve it in criterion 4's favour.

So my resolution across all three designs below is the same: **projection is an optional, opt-in
capability that a domain module registers for itself; the claim store is complete and queryable
without it.** Findings opt in and get `status='in_progress'`, so criterion 6 (existing
`query(status="in_progress")` consumers) is satisfied untouched. Requirements opt out and their
ownership is visible only through the claim store, which satisfies criterion 5 by making it a
non-problem: there were never any `reqs_query(status="in_progress")` consumers to preserve, because
that status has never existed.

---

## 2. Answer to Q2 — where the write lives (shared by all three designs)

`entities.py` stays **read-only and unmodified**. Not one line changes. Adding a third entity kind
still means one `EntityKind` entry at `entities.py:36-55`, and `tests/test_entities.py:147-152`
keeps its exact current scope.

The claim write lives in a **new self-registering domain module** (`claims.py` / `sidecar.py` /
`worklog.py` depending on the design). That module interpolates **zero identifiers**:

- Its own table names are literals inside its module-level `SCHEMA` string, exactly like every
  other module (`findings.py:15-37`, `reqs.py:15-36`, `milestones/_schema.py:52-77`).
- Every claim query is fully parameterized on `entity_id` / `holder` / `kind`. `kind` is stored as
  a *value* (`EntityRef.of(id).kind.name`), never as a table name.
- **The status projection is not in the claims module at all.** It is a callback the owning domain
  module registers for itself, and that callback writes `UPDATE findings SET ... WHERE id = ?`
  with a literal table name inside `findings.py`. The claims module never learns that `findings`
  is a table.

The rejected alternative is instructive: I could have put `UPDATE {kind.table} SET status=?` inside
the claims module. That would be *legal* — it is precisely the `blockers.py:437-438` pattern with
its `noqa` — but it would (a) put a second copy of registry-identifier interpolation in the repo,
and (b) hard-code the assumption that every kind has a projectable status, breaking criterion 4.
The callback registry avoids both. Interpolation count added by my design: **zero.**

Reads for the entity's existence go through the sanctioned path: `EntityRef.of(entity_id).require(conn)`
(`entities.py:105-108`). That is the only cross-table read my module performs, and it is
`entities.py`'s own.

---

## 3. Cross-cutting mechanics (shared by all three designs)

### 3.1 The fourth outcome (C4)

The three-outcome contract is wrong. `db.connect()` (`db.py:492-503`) never mentions
`busy_timeout`; the 5000 ms that produces a clean `rowcount == 0` is `sqlite3.connect(timeout=5.0)`'s
undocumented default. The research measured ~1400 `OperationalError: database is locked` per 200
trials once that default is removed.

Every claim/release/steal entry point is wrapped:

```python
_BUSY_MS = 5000  # explicit: db.connect() only inherits this from sqlite3's default

def _with_busy_timeout(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout={_BUSY_MS}")

def _undetermined(exc: sqlite3.OperationalError) -> dict[str, Any]:
    msg = str(exc).lower()
    if "locked" not in msg and "busy" not in msg:
        raise exc                      # a real error, not contention — never masked
    return {
        "outcome": "undetermined",
        "reason": "database_busy",
        "retry_after_ms": 250,
        "detail": str(exc),
    }
```

**`undetermined` means: the database was too contended to tell you. The claim may or may not have
been made. Re-issue the identical call — it is idempotent, and the retry will return `claimed`,
`already_mine`, or `held_by_other`.** Idempotency is what makes `undetermined` safe rather than
alarming: the same `(entity_id, holder)` pair replayed can only ever converge, never double-claim.
That is the whole reason I insist on an idempotent claim primitive rather than a bare INSERT.

Callers get an explicit contract: `undetermined` is retryable, `held_by_other` is not.

### 3.2 Never a plain `BEGIN`; a shared helper instead of a fourth copy

Any design of mine whose claim is more than one statement uses `BEGIN IMMEDIATE`, never plain
`BEGIN` (research C3: a plain `BEGIN` pins a read snapshot and the upgrade dies with
`SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` cannot rescue).

The isolation save/restore dance at `capacity.py:179-218` is currently a one-off. I add a shared
context manager to `db.py` — infrastructure, allowed, no domain import:

```python
@contextmanager
def immediate_txn(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN IMMEDIATE with isolation_level save/restore. Never write a plain BEGIN."""
    saved = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = saved
```

`capacity.py:179-218` is refactored onto it in the same PR (mechanical, behaviour-identical,
covered by the existing race test at `tests/test_milestones.py:801`).

### 3.3 Referential integrity with `PRAGMA foreign_keys = 0` (C5)

**No `REFERENCES` clause appears anywhere in my schema.** Writing one would be a lie in DDL —
it reads as enforced and is not. Integrity is maintained by three concrete mechanisms instead:

1. **Write-time validation.** `claim()` calls `EntityRef.of(entity_id).require(conn)`
   (`entities.py:105-108`) before any insert. An unknown-format ID raises `ValueError` from
   `EntityRef.of` (`entities.py:78`); a well-formed but absent ID raises `KeyError`. This matches
   the module error contract in `CLAUDE.md`. A claim on a non-existent entity is therefore
   impossible to create through the API.
2. **Read-time orphan reporting.** `claims_status()` / `claims_list()` evaluate
   `EntityRef.of(row["entity_id"]).exists(conn)` per row and emit `"orphaned": true` for rows whose
   entity has since been deleted. Orphans are *reported*, never silently dropped — consistent with
   the user's "report, never auto-steal" stance. A `claims_prune(dry_run=True)` tool deletes them
   only on explicit request.
3. **Precedent that this is the house style.** `milestones/triage.py` already catches `KeyError`
   for a deleted finding rather than relying on FK cascade. The codebase has met this situation
   before and handled it in Python.

Note the same reasoning applies to `milestone_items.milestone_id TEXT NOT NULL REFERENCES
milestones(id)` at `_schema.py:54` — that clause is already decorative today. I am not introducing
a new hazard; I am declining to add a fourth decorative one.

### 3.4 Second-resolution timestamps break the obvious discriminator

`types.utc_now()` (`types.py:12-14`) formats to whole seconds. The research's probe distinguished
`claimed` from `already_mine` by comparing `claimed_at < renewed_at`. **Two calls inside the same
wall-clock second produce equal strings and the retry would be misreported as a fresh claim.** A
retrying agent hitting a 200 ms loop does exactly this. I therefore discriminate on a monotonic
integer (`touch_count` / `revision`) that the upsert increments, never on timestamps. This is a
concrete correction to the executed probe, and it is why my `RETURNING` list includes a counter.

---

## Solution 1 — **Claim Ledger**

### Core Idea

One new module, `claims.py`, owning one table with one row per *currently held* entity, keyed by
`entity_id`. A claim is a single idempotent upsert whose `RETURNING` clause tells the caller which
of the four outcomes occurred. The claims module knows nothing about `findings` or `requirements`
— it validates entity IDs through `entities.EntityRef` and delegates any status projection to a
callback that the owning domain module registers for itself. Ownership gets a real home, that home
is 90 lines, and the entity tables never learn it exists.

### How It Works

**Schema** (`claims.py`, module-level `CLAIMS_SCHEMA`, registered via `db.register_schema`):

```sql
CREATE TABLE IF NOT EXISTS entity_claims (
    entity_id    TEXT PRIMARY KEY,          -- 'CB-1234' | 'FR-7' | future kinds
    kind         TEXT NOT NULL,             -- EntityKind.name, a VALUE not an identifier
    holder       TEXT NOT NULL,             -- agent id, opaque
    claimed_at   TEXT NOT NULL,
    renewed_at   TEXT NOT NULL,             -- heartbeat, bumped free by already_mine
    touch_count  INTEGER NOT NULL DEFAULT 1,
    note         TEXT NOT NULL DEFAULT '',
    prev_status  TEXT,                      -- pre-claim status, only if projected
    projected_to TEXT                       -- the status we wrote, or NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_holder ON entity_claims(holder);
CREATE INDEX IF NOT EXISTS idx_claims_kind   ON entity_claims(kind);
```

No `REFERENCES` (§3.3). `PRIMARY KEY(entity_id)` *is* the mutual-exclusion primitive.

**The claim path — exact SQL, one statement:**

```sql
INSERT INTO entity_claims
    (entity_id, kind, holder, claimed_at, renewed_at, touch_count, note, prev_status, projected_to)
VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
ON CONFLICT(entity_id) DO UPDATE SET
    renewed_at  = excluded.renewed_at,
    touch_count = entity_claims.touch_count + 1
  WHERE entity_claims.holder = excluded.holder
RETURNING holder, claimed_at, renewed_at, touch_count, projected_to;
```

Outcome decision, entirely from this statement's result:

| `RETURNING` result | Outcome |
|---|---|
| one row, `touch_count == 1` | `claimed` |
| one row, `touch_count > 1` | `already_mine` (and `renewed_at` just moved — free heartbeat) |
| no rows | `held_by_other` → one parameterized `SELECT` names the holder |
| `OperationalError` (locked/busy) | `undetermined` (§3.1) |

**Transaction shape.** Without projection the claim is one statement and needs no ceremony
(research Q3d). With projection it is two, so it runs inside `db.immediate_txn(conn)` (§3.2). The
projector must not commit — it is called mid-transaction.

**Projector registry** — this is the whole answer to Q2 and criterion 4:

```python
# claims.py
Projector = Callable[[sqlite3.Connection, str, str | None], str | None]
#            (conn, entity_id, target_status_or_None_to_restore) -> prior status

_projectors: dict[str, Projector] = {}

def register_projector(kind_name: str, fn: Projector, *, busy_status: str) -> None:
    """A domain module opts its own kind into status projection. Idempotent by name,
    matching db.register_post_add_hook discipline (db.py:188-190)."""
```

```python
# findings.py, module level — alongside its existing register_schema / register_tool_provider calls
def _project_claim(conn, finding_id: str, target: str | None) -> str | None:
    """Set or restore findings.status for a claim. Runs inside the caller's
    transaction and MUST NOT commit. Returns the prior status."""
    row = conn.execute("SELECT status FROM findings WHERE id = ?", (finding_id,)).fetchone()
    prior = row["status"] if row else None
    if target is not None and prior not in types.FINDING_TERMINAL:
        conn.execute(
            "UPDATE findings SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (target, types.utc_now(), finding_id, prior),
        )
    return prior

claims.register_projector(types.ENTITY_FINDING, _project_claim, busy_status="in_progress")
```

`reqs.py` registers nothing. Kind #3 registers nothing unless it wants to. Literal table name,
inside the module that owns the table, zero interpolation.

**Public API** (`claims.py`, keyword-only after `conn` per `CLAUDE.md`):

```python
def claim(conn, *, entity_id: str, holder: str, note: str = "",
          project: bool = True) -> dict[str, Any]
def release(conn, *, entity_id: str, holder: str,
            restore_status: bool = True) -> dict[str, Any]
def steal(conn, *, entity_id: str, holder: str,
          expected_holder: str, reason: str) -> dict[str, Any]
def who_holds(conn, *, entity_id: str) -> dict[str, Any] | None
def held_by(conn, *, holder: str) -> list[dict[str, Any]]
def list_claims(conn, *, kind: str | None = None, holder: str | None = None,
                stale_after_seconds: int | None = None) -> dict[str, Any]
```

**Release** — exact SQL, and the answer to Q5:

```sql
DELETE FROM entity_claims WHERE entity_id = ? AND holder = ?
RETURNING prev_status, projected_to;
```
Row returned → `released`. No row, but a `SELECT` finds one → `not_yours` (with the real holder).
No row at all → `not_claimed`. Then, only if `projected_to` is not NULL, the projector restores
with a guard: `UPDATE findings SET status = ? WHERE id = ? AND status = ?` bound to
`(prev_status, entity_id, projected_to)`. **If the holder already moved the entity to `fixed`, the
guard's `status = projected_to` fails, `rowcount == 0`, and the status is left alone.** Release
never resurrects finished work. The response reports `status_restored: false, current_status: "fixed"`.

**Steal** (explicit opt-in only, per the user's "never auto-steal"):

```sql
UPDATE entity_claims
   SET holder = ?, claimed_at = ?, renewed_at = ?, touch_count = 1, note = ?
 WHERE entity_id = ? AND holder = ?
RETURNING holder;
```
The `holder = ?` guard is bound to the caller-supplied `expected_holder`, so a steal is a
compare-and-swap against the holder the caller actually observed. If the claim changed hands
between the caller's read and its steal, `rowcount == 0` → `held_by_other`, no lost update.
Requiring `expected_holder` also makes accidental stealing impossible: you cannot steal without
first having looked.

**Outcome vocabulary (complete):**

`claim` → `claimed` | `already_mine` | `held_by_other` | `undetermined`
`release` → `released` | `not_yours` | `not_claimed` | `undetermined`
`steal` → `stolen` | `held_by_other` | `not_claimed` | `undetermined`

Every response dict carries `entity_id`, `kind`, `holder`, `claimed_at`, `renewed_at`,
`touch_count`, `held_seconds`, `projected_status`, `orphaned`. `undetermined` additionally carries
`retry_after_ms` and `detail`.

### C1 — what happens when `provenance.py:265-270` fires on a claimed entity

Concretely: agent-7 claims CB-1234 → `entity_claims` row exists, `findings.status='in_progress'`,
`prev_status='open'`, `projected_to='in_progress'`. Agent commits `Fixes: CB-1234`.
`provenance.py:261` checks `current["status"] in types.FINDING_TERMINAL` — `in_progress` is **not**
terminal (`types.py:36`), so it does not skip; `:265-270` calls `update_finding(status='fixed')`.
The claim row is in a different table and is untouched. Without further work you get a live claim
on a `fixed` finding.

**My design handles this by name, at the choke point, not by asking provenance to be careful.**
`findings.py:298` is the single UPDATE through which *all four* status writers pass (the MCP tool
at `:596`, the CLI, `triage.py`, and `provenance.py:265`). I add a hook seam there, mirroring the
existing `register_post_add_hook` / `run_post_add_hooks` pair (`db.py:178-204`):

```python
# db.py — infrastructure, no domain import
def register_post_update_hook(name, fn: Callable[[sqlite3.Connection, str, str | None, str | None], None]) -> None
def run_post_update_hooks(conn, entity_id: str, old_status: str | None, new_status: str | None) -> None
```
called from `findings.update_finding` immediately before its `conn.commit()` (`findings.py:298-299`),
so hook effects land in the same transaction as the status write.

`claims.py` registers one hook: **if the new status is in `EntityRef.of(entity_id).kind.terminal`
(`entities.py:41`/`:50`) and a claim row exists, delete it and record an auto-release.** So the
provenance sequence ends with CB-1234 `fixed` and unclaimed — the correct state — and the release
was caused by the commit trailer, which is exactly what happened.

Two deliberate details:
- I keep `run_post_add_hooks`'s **exception-swallowing** semantics (`db.py:201-204`). A failed
  auto-release must not break a status write. The degraded state is a stale claim on a terminal
  entity, which `claims_status` already reports as `divergent` — the failure mode lands inside the
  design's existing reporting, not outside it.
- Independently of the hook, `claims_status` and `claims_list` compute
  `divergent = entity.is_resolved(conn) and claim_exists`. **So if the hook seam is rejected in
  review, C1 degrades to "visible and reportable" rather than "silently wrong."** The design has a
  fallback that needs no `db.py` change at all.

### Pros

- **Smallest surface that meets all nine criteria.** One table, one module, ~200 lines including
  tools and CLI.
- **Zero new identifier interpolation, `entities.py` unmodified.** (§2)
- **Criterion 4 is literally free**: kind #3 is one `EntityKind` entry; the claim store stores
  `kind` as a value and branches on nothing.
- **Criterion 5 dissolved rather than worked around**: requirements need no CHECK migration, and
  the design proves projection is optional — which is what criterion 4 required all along.
- **Criterion 3 is two indexed point queries.** `who_holds` is the PK; `held_by` is
  `idx_claims_holder`. No window fold, no 752 ms reverse query.
- **Criterion 7 free**: `already_mine` bumps `renewed_at` at no extra cost, so "stale" means
  "hasn't checked in", not "claimed long ago". Reader chooses the threshold; nothing is baked in.
- **`undetermined` is safe because the primitive is idempotent** — replaying the identical call
  can only converge.
- Fixes a latent bug in the executed probe (second-resolution timestamps, §3.4).
- Ships a shared `db.immediate_txn` that retires the one-off dance at `capacity.py:179-218`.

### Cons

- **No history.** Release is a `DELETE`. "Who held CB-1234 last week" is unanswerable. If audit
  value is wanted, this is the wrong design and Solution 3 is right.
- **Second representation of ownership** alongside `milestone_items.assigned_agent`
  (`_schema.py:64`) until convergence lands. Two places to look, and a period where they can
  disagree.
- **The `db.register_post_update_hook` seam is new infrastructure** justified by exactly one
  consumer. Reviewers may reasonably call that speculative; the fallback (read-time divergence
  reporting) exists but is weaker.
- **`prev_status` is a snapshot, not a lock.** Between claim and release, anything may move the
  status; the guarded restore handles it correctly but silently. The caller must read
  `status_restored` to know.
- **Does not answer Q6.** There is no transition layer, no "who may transition what". If the user
  meant it when they said this looks like the seed of a workflow, this design does not plant it.
- Adds one more `CREATE TABLE IF NOT EXISTS` to every `db.connect()` (`db.py:500-501`) — trivial,
  but the pattern of "every connection re-runs every schema" is now one module worse.

### Effort: **M**

`claims.py` ~200 lines (schema, 6 public functions, ~7 MCP tools, CLI). `db.py` +
`immediate_txn` + the update-hook pair (~40). `findings.py` + projector + tool param (~30).
`capacity.py` refactor onto `immediate_txn` (~15, mechanical). `tests/test_claims.py` ~250,
including a two-connection race test cloned from `tests/test_milestones.py:801-846`. Wiring: 3
one-line edits (`db.py:487`, `server.py:22-32`, `cli.py:49`).

### Risk Profile

- **Correctness: low.** The substrate (`INSERT … ON CONFLICT DO UPDATE … WHERE`) was executed
  across 4 real processes × 200 entities with zero violations. Correctness is settled for all
  substrates (C2); I claim no advantage there.
- **Adoption: moderate — this is the real risk.** `pull_next` (`capacity.py:167`) is a correct
  primitive that nothing calls. §"Adoption" below is the mitigation and it must be treated as part
  of the deliverable, not as follow-up work.
- **Divergence with `milestone_items`: moderate**, mitigated by the convergence plan and a
  consistency test.
- **Schema regret: low.** `entity_claims` is a strict subset of Solution 3's table. Upgrading later
  is `ALTER TABLE ADD COLUMN session_id / released_at` plus a new `agent_sessions` table — additive,
  per `CLAUDE.md`. **I am not painting into a corner.**

---

## Solution 2 — **Virtual Column Sidecar**

### Core Idea

My forbidden lever is not an artificial handicap — it is the permanent condition of this codebase.
Changing a status CHECK costs a full table rebuild (`findings.py:51-97`, 45 lines, for one enum
value). So the capability actually worth building is not "a claims table" but **"attach mutable,
exclusively-settable state to any entity without touching its table."** Ownership is then the first
attribute, not the only one. One generic store, one generic compare-and-swap, and `owner` is a row
in it.

### How It Works

**Schema** (`sidecar.py`):

```sql
CREATE TABLE IF NOT EXISTS entity_attrs (
    entity_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    attr       TEXT NOT NULL,        -- 'owner', later: 'wip_branch', 'review_state', ...
    value      TEXT NOT NULL,
    set_at     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 1,
    meta       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_id, attr)
);
CREATE INDEX IF NOT EXISTS idx_attrs_lookup ON entity_attrs(attr, value);
CREATE INDEX IF NOT EXISTS idx_attrs_kind   ON entity_attrs(kind, attr);
```

**The exclusive-set path — exact SQL, one statement, generic over any attribute:**

```sql
INSERT INTO entity_attrs (entity_id, kind, attr, value, set_at, updated_at, revision, meta)
VALUES (?, ?, ?, ?, ?, ?, 1, ?)
ON CONFLICT(entity_id, attr) DO UPDATE SET
    updated_at = excluded.updated_at,
    revision   = entity_attrs.revision + 1
  WHERE entity_attrs.value = excluded.value
RETURNING value, set_at, updated_at, revision;
```

`claim(entity, holder)` is `set_exclusive(entity_id, attr="owner", value=holder)`. Outcome mapping
is identical to Solution 1, keyed on `revision` (== 1 → `claimed`, > 1 → `already_mine`, no row →
`held_by_other`, `OperationalError` → `undetermined`). `set_at` is the claim time, `updated_at` the
heartbeat.

**Declared attribute registry** — the capability-first part:

```python
@dataclass(frozen=True)
class AttrSpec:
    name: str
    exclusive: bool                 # True => CAS on set; False => last-write-wins
    clear_on_terminal: bool         # auto-remove when the entity reaches a terminal status
    applies_to: frozenset[str] | None   # kind names; None = every kind

ATTRS: tuple[AttrSpec, ...] = (
    AttrSpec("owner", exclusive=True, clear_on_terminal=True, applies_to=None),
)
```

Adding a second per-entity concern later is one tuple entry, no new table, no migration. That is
the pitch.

**Module boundary (Q2):** identical to Solution 1. `sidecar.py` interpolates nothing —
`entity_attrs` is a literal in its own SCHEMA, `attr` and `value` are parameters. Status projection
still goes through the same domain-registered projector callback (`findings.py` writes its own
literal `UPDATE findings`). `entities.py` unmodified.

**Transaction shape:** one statement bare; with projection, `db.immediate_txn` (§3.2).

**Criterion 3 both directions, indexed:** `who holds X` is the PK
(`WHERE entity_id=? AND attr='owner'`); **`what does agent-7 hold` is
`WHERE attr='owner' AND value=?` on `idx_attrs_lookup` — a covering index seek.** That is the query
the research measured at 82 ms / 752 ms against an append-only fold; here it is sub-millisecond and
stays sub-millisecond, because the store holds only *live* attributes.

**Outcome vocabulary:** same four for claim, plus generic `set` → `set` | `unchanged` | `conflict` |
`undetermined` for non-exclusive attributes.

### C1 — what happens when `provenance.py:265-270` fires on a claimed entity

Same seam as Solution 1 (`db.register_post_update_hook` called from `findings.py:298`), but the
hook is **generic**: on a terminal status it deletes every attr whose `AttrSpec.clear_on_terminal`
is True. So `owner` is dropped, and any future attribute inherits the correct behaviour by
declaration rather than by someone remembering. This is genuinely better than Solution 1's
special-cased release — one rule, all attributes.

Same fallback: `sidecar_status` reports `divergent` when an entity is terminal and still carries a
`clear_on_terminal` attr, so C1 degrades to visible rather than wrong if the seam is rejected.

### C5 — referential integrity

Identical to §3.3: no `REFERENCES`, `EntityRef.require()` at write time, `exists()` at read time,
orphans reported. One extra wrinkle: an orphan sweep here must delete *all* attrs for a missing
entity, not one row — a `DELETE FROM entity_attrs WHERE entity_id = ?`.

### Pros

- **The only design where the forbidden lever is the thesis instead of the obstacle.** Three tables
  are effectively frozen by migration cost; this makes that permanently survivable.
- **Second and third concerns are free.** `wip_branch`, `review_state`, `deferred_until` need no
  table, no migration, no module.
- **Best reverse query of the three** — `(attr, value)` index turns criterion 3's hard direction
  into a seek.
- `clear_on_terminal` as a declared property means C1 is handled by rule, not by a special case.
- Criteria 4 and 5 as free as in Solution 1.

### Cons — and I think they are disqualifying

- **This is EAV, and EAV's costs are well known and real here.** `value` is untyped `TEXT`. No
  per-attribute CHECK, no per-attribute index tuning, no way to say "owner must look like an agent
  id". The repo's `types.py` exists precisely to keep this kind of value stringly-typed-but-validated,
  and B routes around it.
- **It contradicts my own stated position.** I argued ownership deserves its own home. This gives
  it a rented room with a nameplate reading `attr='owner'`. Every consumer must know that magic
  string. `CLAUDE.md` calls out stringly-typed cross-module reach as debt elsewhere; this
  institutionalises it.
- **Speculative generality against a C7 bar.** The honest problem is: two agents both get told
  "ok" and neither learns which one moved the row. Answering that with a general attribute-system
  is a large multiple of the problem's size. There is **no evidence in the repo of a second
  demanded attribute** — I looked; `milestone_items` grew real columns rather than a sidecar.
- **Schema-by-convention with no migration story.** When `owner` needs to become `(holder,
  session)`, every existing row is an untyped string you must reinterpret in Python.
- Query ergonomics degrade for humans: every ownership question is a three-predicate query with a
  literal `'owner'` in it.

### Effort: **M/L**

Larger than Solution 1 because the generic layer needs its own validation, its own registry, its
own tests for the non-owner path, and documentation explaining when to use an attribute versus a
real column — a doc that will be ignored.

### Risk Profile

- **Correctness: low** (same executed substrate, PK CAS).
- **Design-debt: high.** This is the design most likely to be regretted in a year, and the
  regret is un-migratable: untyped strings in a shared bag.
- **Review risk: high.** It is the hardest of the three to justify against C7's "API-expressiveness,
  not data-integrity" bar, and a reviewer would be right to press on it.
- **Adoption: same as Solution 1.**

---

## Solution 3 — **Work Sessions**

### Core Idea

Take my own perspective at full strength: ownership is not a field, it is a **relation between an
agent's work session and an entity**, and the session is the thing with the lifecycle. An agent
opens a session, claims entities into it, heartbeats once for all of them, and ends the session
(finished / abandoned / crashed). Staleness becomes a property of the *session*, so one dead agent
is one stale row rather than N stale claims. Released claims stay as rows, so the store is its own
history without becoming an event log you must fold. This is also the "seed of a process" the user
sensed — the session is the workflow spine.

### How It Works

**Schema** (`worklog.py`, two tables):

```sql
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id   TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    ended_at     TEXT,
    end_reason   TEXT              -- 'finished' | 'abandoned' | 'crashed' | NULL while live
);
CREATE INDEX IF NOT EXISTS idx_sessions_live ON agent_sessions(agent_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS entity_claims (
    claim_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id      TEXT NOT NULL,
    kind           TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    agent_id       TEXT NOT NULL,     -- denormalized so the reverse query needs no join
    claimed_at     TEXT NOT NULL,
    renewed_at     TEXT NOT NULL,
    released_at    TEXT,
    release_reason TEXT,              -- 'released' | 'terminal_status' | 'stolen' | 'session_ended'
    prev_status    TEXT,
    projected_to   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_live
    ON entity_claims(entity_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_claim_agent_live
    ON entity_claims(agent_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_claim_entity_hist ON entity_claims(entity_id, claimed_at DESC);
```

The **partial unique index** is the exclusion primitive — the research executed exactly this
substrate (`C_partial`), 4 processes × 200 entities, zero violations.

**The claim path — exact SQL, two statements inside `db.immediate_txn`:**

```sql
-- 1) idempotent re-claim / heartbeat
UPDATE entity_claims
   SET renewed_at = ?, session_id = ?
 WHERE entity_id = ? AND released_at IS NULL AND agent_id = ?;
--    rowcount == 1  -> already_mine (stop here)

-- 2) only if rowcount == 0: take it
INSERT INTO entity_claims
    (entity_id, kind, session_id, agent_id, claimed_at, renewed_at, prev_status, projected_to)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
--    success        -> claimed
--    IntegrityError -> held_by_other   (partial unique index fired)
```

Note the loser's signal here is an **`IntegrityError`**, a different exception shape from the
`rowcount == 0` of the other two designs. That must be caught precisely and distinguished from
`OperationalError` — `IntegrityError` is `held_by_other` (deterministic), `OperationalError`
locked/busy is `undetermined` (retryable). Getting this backwards would turn a permanent loss into
an infinite retry loop, so the design states it explicitly and tests both.

**Session handling — implicit, because agents will not thread a session id.** `claim()` takes
`agent_id` and an optional `session_id`; when omitted it reuses the agent's newest live session
(`idx_sessions_live`) or opens one. Every claim/renew bumps `agent_sessions.heartbeat_at`. So
heartbeating is free and requires no new caller discipline — the thing that would otherwise kill
this design.

**Release** keeps the row:
```sql
UPDATE entity_claims
   SET released_at = ?, release_reason = ?
 WHERE entity_id = ? AND released_at IS NULL AND agent_id = ?
RETURNING claim_id, prev_status, projected_to;
```
Status restore is the same guarded `UPDATE ... AND status = projected_to` as Solution 1 (Q5).

**Criterion 3, both directions, indexed — and this is where Solution 3 shines:**
`who holds X` → `idx_claim_live`. `what does agent-7 hold` → `idx_claim_agent_live`, a partial
index over *live* claims only, so it stays fast as history grows. **`who held X historically`** →
`idx_claim_entity_hist`. The research's 752 ms window fold never appears, because the live set is
materialized by the partial index rather than derived by a fold. This is precisely the "audit
substrate with a mutable projection beside it" that the research called materially more honest —
except there is no separate projection to keep in sync; one table, two indexes.

**Staleness (criterion 7) is a session property.** `worklog_stale(threshold_seconds)` returns
sessions whose `heartbeat_at` is older than the threshold, each with its live claims attached — one
indexed join. A crashed agent shows up as **one** row with N entities, not N unrelated stale claims.
Recovery is `worklog_end_session(session_id, end_reason='crashed', release_claims=True)`, an
explicit human/agent action — never a background process, never automatic (the user's constraint).

**Outcome vocabulary:** `claimed` | `already_mine` | `held_by_other` | `undetermined`, plus
session-level `session_opened` | `session_reused` | `session_ended` | `session_already_ended`.
Responses carry `session_id`, `agent_id`, `heartbeat_age_seconds`, and a `history` list on request.

### C1 — what happens when `provenance.py:265-270` fires on a claimed entity

Same seam (`register_post_update_hook` at `findings.py:298`), but the outcome is the most
informative of the three: the claim is **closed, not deleted**, with
`released_at = now, release_reason = 'terminal_status'`. The row survives and says: agent-7 held
CB-1234 from 14:02 to 15:41, and it ended because a commit trailer moved the finding to `fixed`.
The session stays live and keeps its other claims. **That is a strictly better answer than "delete
the row" — the provenance interaction becomes a recorded fact instead of an erased one**, and it is
the single strongest argument for this design over Solution 1.

### C5 — referential integrity

Two edges, neither enforced by the engine:
- `entity_claims.entity_id` → entity tables: same as §3.3 (`require()` on write, `exists()` on
  read, orphans reported).
- `entity_claims.session_id` → `agent_sessions.session_id`: **this one I control entirely.**
  Sessions are only created by `worklog.py` and are never deleted (ended, not removed), so the edge
  cannot dangle by construction. A test asserts no claim references a missing session. No
  `REFERENCES` clause is written, for the same honesty reason as everywhere else.

### Pros

- **The only design that keeps history without paying the fold cost.** `released_at IS NULL` +
  partial indexes give live-set queries at point-lookup speed and full history for free.
- **Staleness scales with agents, not with claims.** One heartbeat covers everything an agent
  holds; one crashed agent is one row to look at.
- **Best C1 answer** — the provenance interaction is recorded rather than erased.
- **Best convergence story with `milestone_items`** (below): a session is the natural home for
  `pull_next`'s capacity accounting too.
- **It is the workflow seed the user actually sensed.** If Q6 is answered "yes, build the process
  layer", sessions + claim history is the spine transitions would hang from.
- Criteria 4 and 5 as free as the others; `entities.py` untouched; zero interpolation.

### Cons

- **Effort and surface roughly double Solution 1** for a problem C7 explicitly downgraded to
  "API-expressiveness". That is the central charge against it and I do not have a rebuttal beyond
  "the extra buys history and lifecycle" — which is only worth it if those are wanted.
- **Sessions are a concept agents must now understand**, even though implicit-session handling hides
  most of it. Implicit reuse has its own sharp edge: an agent that never ends a session accumulates
  one that lives forever, and "stale session" becomes noisy.
- **Two exception shapes to get right** (`IntegrityError` vs `OperationalError`). Solution 1 has
  only one path.
- **`AUTOINCREMENT` + history means unbounded growth.** At this project's scale that is a
  non-problem (the research measured 30 MB at 500k rows), but it is a real difference from
  Solution 1's bounded table.
- **Most speculative against present evidence.** The brief records that the race has *never been
  observed firing*; the cost seen was duplicated work. Building a session subsystem for that is the
  hardest sell in this document, and I would rather say so than bury it.
- Two more `CREATE TABLE IF NOT EXISTS` per `db.connect()`.

### Effort: **L**

`worklog.py` ~400 lines (2 tables, session lifecycle, claim/release/steal, ~11 MCP tools, CLI).
Tests ~400 including race tests for both the `IntegrityError` and `OperationalError` paths, session
reuse, and crash recovery. Plus the shared `db` changes from §3.

### Risk Profile

- **Correctness: low** (partial-unique substrate executed, zero violations) — but **higher than the
  other two in implementation risk**, because two statements, two exception types and implicit
  session selection are three more places to be subtly wrong.
- **Scope risk: high.** This is the design most likely to consume the whole budget and then arrive
  unwired, repeating the `pull_next` failure (fact 2) at 3× the size.
- **Regret risk: low if built, high if half-built.** Sessions are all-or-nothing; a session table
  nobody opens is worse than no session table.

---

## 4. Cross-cutting: convergence with `milestone_items` (Fact 6 / criterion 8)

`milestone_items.assigned_agent` (`_schema.py:64`) + `pulled_at` (`:65`) + partial
`idx_mi_assigned` (`:77`), written by `capacity.py:196-201` and cleared at `:240-241` / `:247`, is
the first representation of ownership. My store is the second. My lever forbids me from touching
that table, so convergence has to be behavioural. The plan, identical in shape for all three
designs:

1. **The claim store becomes the source of truth for generic ownership.** `assigned_agent` is
   redesignated a denormalized cache of the milestone-scoped view.
2. **`pull_next` writes both, atomically, with no schema change.** `capacity.py:167-220` already
   runs inside `BEGIN IMMEDIATE` (`:182`) and already writes three things (item UPDATE `:196-201`,
   capacity `:202`, audit `:203-212`). Adding a fourth statement — a `claims.claim()` on the item's
   `item_ref` — is one line in an existing transaction. `item_ref` is a `CB-`/`FR-` id for
   `item_kind IN ('bug','requirement')` (`_schema.py:55-57`), so `EntityRef.of` resolves it
   directly.
3. **`item_kind='external'` is the honest gap.** External items have no entity id and cannot be
   claimed by the generic store; they keep `assigned_agent` alone. I state this rather than paper
   over it.
4. **A consistency test** asserts that for every `milestone_items` row with a non-null
   `assigned_agent` and a resolvable `item_ref`, `who_holds(item_ref).holder == assigned_agent`.
   This is what turns "two representations" from a latent bug into a checked invariant during the
   transition.
5. **Later (out of scope):** `capacity.py` reads the holder from the claim store and a migration
   drops `assigned_agent`, `pulled_at`, and `idx_mi_assigned`.

**The part I will not pretend is solved:** `pull_next`'s ownership is *capacity-aware and
size-gated* (`_eligibility_failure` at `capacity.py:187`, `_upsert_capacity_increment` at `:202`).
A generic claim carries none of that. Subsuming `milestone_items` means either absorbing capacity
accounting into the claim store — which I reject, it is milestone policy, not ownership — or
accepting that `assigned_agent` means "pulled under capacity" while a claim means "held". Those are
different predicates. My plan keeps them different and makes the *holder* agree; it does not
collapse the semantics. Anyone claiming full subsumption is overselling.

---

## 5. Cross-cutting: adoption (Q7 / C11) — the exact file and the exact change

This is where `pull_next` died (fact 2) and it is the single highest risk in this document, higher
than any technical risk above. Three named changes, all required in the first delivery:

**(1) `src/codebugs/findings.py:573-581` — the MCP `update` tool gains one parameter.**

```python
    @mcp.tool()
    def update(
        finding_id: str,
        status: str | None = None,
        agent_id: str | None = None,      # NEW
        notes: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
```

When `agent_id` is supplied **and** `status` normalizes (via `types.resolve_finding_status`,
`types.py:87-89`) to `'in_progress'`, the tool routes through `claims.claim()` instead of writing
the status directly, and returns the outcome under a `"claim"` key. On `held_by_other` it **does
not write the status** and returns the refusal with the real holder. When `agent_id` is omitted,
behaviour is byte-identical to today — no existing caller breaks.

Why this call site and not a shiny new `claim` tool: **the claim path becomes the path agents
already use.** There is nothing new to remember, which is exactly the property `pull_next` lacked.
The tool already does entity-layer work post-update (`findings.py:605-609`), so this is not a new
kind of responsibility for it.

**(2) `~/.claude/skills/fix-latest-codebugs/SKILL.md:92` — replace an instruction that cannot work.**

The current line is `mcp__codebugs__update(id="CB-1234", status="in_progress", assignee="claude")`.
I opened the signature at `findings.py:574-581`: there is no `id` parameter and no `assignee`
parameter. **This documented claim call has never been able to succeed.** It becomes:

```
1. `mcp__codebugs__update(finding_id="CB-1234", status="in_progress", agent_id="<your agent id>")`
   — claims the bug. Check the `claim.outcome` field:
     • `claimed`       → proceed.
     • `already_mine`  → you are resuming your own work; proceed.
     • `held_by_other` → STOP. Report the holder to the user and offer the next candidate.
     • `undetermined`  → the DB was busy. Re-issue the identical call after ~250 ms.
```

C11 is right that this is the strongest lever available: the file must be edited no matter what,
because it is currently wrong. Attaching to a required edit beats adding an optional tool.

**(3) Wiring, three one-line edits.** `db.py:487` add the module to the `_ensure_modules_loaded`
import list; `server.py:22-32` add `"claims": "codeclaims"`; `cli.py:49` add `"claims"` to the
`--mode` choices. Per `CLAUDE.md`'s "Current rules for new code".

**Definition of done for adoption:** a test asserts that calling the MCP `update` tool twice with
`status="in_progress"` and two different `agent_id`s yields `claimed` then `held_by_other`. If that
test does not exist, the feature is not delivered — regardless of how complete `claims.py` is.

---

## 6. Testing (criterion 1)

Follow the existing precedent rather than invent a harness:
`tests/test_milestones.py:801-846` (`test_two_threads_two_connections_no_double_claim`) — two real
threads, each with its own `db.connect(tmp_project)` against a file DB, `threading.Barrier(2)`,
assert uniqueness. A second precedent at `tests/test_sweep.py:754-799` uses 10 threads.

Three deliberate choices for `tests/test_claims.py`:
- **Assert the winner count, and assert the loser's outcome string.** Existing tests only assert
  uniqueness. Mine must assert that the loser received `held_by_other` and can name the holder —
  that is the entire point of the feature and uniqueness alone does not test it.
- **A `busy_timeout=0` test** that asserts `undetermined` is returned rather than
  `OperationalError` escaping. This is the only way C4 stays fixed; without it the fourth outcome
  is documentation.
- **A same-second retry test** — two `claim()` calls with mocked `utc_now` returning an identical
  string — asserting `already_mine`. This is the §3.4 timestamp trap, and it would pass on the
  probe's implementation and fail on any timestamp-based discriminator.

---

## 7. My Recommendation: **Solution 1 — Claim Ledger**

Ranked: **1 (Claim Ledger) > 3 (Work Sessions) >> 2 (Virtual Column Sidecar)**.

**Why 1.** C7 settled the bar: this is an API-expressiveness problem, not a data-integrity one. Two
agents writing `in_progress` produce a correct row; only information is lost. Against that bar the
right answer is the smallest thing that is a genuine *capability* rather than a patch — and
Solution 1 is exactly that. It gives ownership its own module, its own table, its own vocabulary
and its own lifecycle, satisfies all nine success criteria, adds zero identifier interpolation,
leaves `entities.py` untouched, and does it in one table and roughly 200 lines. Fact 2 says the
scarce resource in this codebase is not correct primitives — there is already an unused one — it is
*adoption*. Solution 1 spends the least budget on substrate and the most on §5.

**Why not 3, and the exact condition under which I change my mind.** Solution 3 is the better
*design* and I want that recorded. It records the provenance interaction instead of erasing it, its
staleness model scales with agents rather than claims, and it is the workflow seed the user sensed.
But it is roughly double the surface for a problem the brief itself notes **has never been observed
firing**, and it is the design most likely to arrive complete-but-unwired — repeating fact 2's
failure at larger scale. **The trigger to build 3 instead: if Q6 is answered "yes, build the
process layer", or if anyone asks for claim history / post-hoc audit ("who was working on this
last Tuesday"), Solution 1 cannot answer and Solution 3 should be built directly.** Crucially,
Solution 1's `entity_claims` is a strict column-subset of Solution 3's, so the upgrade is additive
(`ADD COLUMN session_id, released_at, release_reason` + a new `agent_sessions` table) and complies
with `CLAUDE.md`'s additive-schema rule. Choosing 1 now does not foreclose 3 later; that
non-foreclosure is a large part of why I choose it.

**Why not 2, plainly.** It is the design I would build if the *general* problem — attaching mutable
state to frozen tables — had evidence behind it. It does not: I found no second demanded attribute
in the repo, and `milestone_items` grew real columns rather than a sidecar when it needed state.
Building an EAV store to solve a naming problem trades a named concept for a magic string at
exactly the moment the user asked for the concept to be named. It contradicts my own brief, and I
include it because it is the honest strongest form of the "capability-first" position taken to its
limit — and because seeing that limit is what makes Solution 1's scope defensible rather than
merely small.

**One thing I want the adversary to attack hardest**, because I am least sure of it: the
`db.register_post_update_hook` seam in §C1. It is new infrastructure with exactly one consumer, and
`db.py`'s existing hook (`db.py:193-204`) swallows exceptions in a way I have chosen to copy. If
that seam is rejected, C1 degrades to read-time divergence reporting — visible but not corrected —
and I would rather be told that now than discover it in review.
