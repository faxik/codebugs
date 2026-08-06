# Architect A — Minimize-Change / In-Place

**Lever:** may not create any new table. Ownership must be expressed through existing tables,
additive columns, existing `meta` JSON, the existing status vocabulary, and the `EntityKind`
descriptor.

**Verdict on the constraint up front:** it does *not* make the problem unsolvable. It makes it
*smaller*. The reason is structural and I want it stated before the proposals, because it is the
one genuine advantage my lever has over any store-it-elsewhere design:

> The brief's hard constraint says the claim **projects into the entity's status**. Status lives on
> the entity row. If ownership also lives on the entity row, claim-plus-projection is **one UPDATE
> statement on one table** — and per `04-research.md:214-217` and `:239-241`, a single guarded
> statement needs no transaction ceremony at all, and cannot desynchronize from its own projection
> because there is nothing to desynchronize. Any design that puts ownership in a different table
> from status has a two-table write, therefore needs `BEGIN IMMEDIATE`, therefore has a divergence
> window it must engineer away. My lever deletes that whole class of work by construction.

Everything below is verified against the repo this run. Line citations were opened by me
personally; where I am relying on the brief or the researcher rather than my own read, I say so
inline.

---

## Verification notes (things I checked that differ from what was handed to me)

1. **`_SAFE_IDENT` is not dead, and it is not a runtime guard either.** `entities.py:20` defines it;
   the only references are `tests/test_entities.py:149-152`, which assert that every declared kind's
   `table`, `sort_col`, and every member of `readable_cols` match it. The runtime guard in
   `EntityRef._read` is the membership check at `entities.py:83`. This matters for all three
   proposals: **a declared claim-column name automatically inherits a test-enforced identifier
   invariant** if I put it in the descriptor. That is a free safety property, not a new one to build.

2. **C1's line range is off by one at the tail.** `provenance.py:261-263` is the terminal-status
   skip; `provenance.py:264` is `if not dry_run:`; the actual write is
   `provenance.py:265-270`, and it is a call to `findings.update_finding(..., status=status_input,
   append_note=...)`, not raw SQL. Opened this run. The enclosing function is
   `resolve_trailers` (`provenance.py:222-272`). Its only non-test caller is its own CLI handler —
   it is **not** an MCP tool. That narrows the exposure and I use it below.

3. **`query_requirements` has no `meta_key` / `meta_value` filter.** `reqs.py:235-249` — its filter
   set is `id, ids, status, priority, section, search, source, tag, group_by, limit, offset`.
   `query_findings` *does* have one (`findings.py:365-371`, `json_extract(meta, ?)`). This
   asymmetry is decisive against Proposal 2 and I score it there.

4. **`utc_now()` is second-resolution** — `types.py:12-14`,
   `strftime("%Y-%m-%dT%H:%M:%SZ")`. The researcher's verified claim/renew contract
   (`04-research.md:317-327`) distinguishes `claimed` from `already_mine` by comparing
   `claimed_at` against `renewed_at`. **With a one-second clock that comparison is ambiguous for a
   same-holder re-claim inside the same second.** The researcher's probe used hand-written
   `'10:00'` / `'10:11'` literals and so never exercised it. All three of my proposals therefore
   specify a millisecond clock for the claim timestamps. See "Shared mechanics" §3.

---

## Shared mechanics (all three proposals depend on these)

### 1. Outcome vocabulary — four cases, per C4

```python
CLAIM_OUTCOMES = ("claimed", "already_mine", "held_by_other", "undetermined")
```

| outcome | meaning | caller should |
|---|---|---|
| `claimed` | this call made the transition | proceed with the work |
| `already_mine` | same holder, claim refreshed (`renewed_at` bumped) | proceed; this is the retry path |
| `held_by_other` | someone else holds it; `holder` and `claimed_at` are reported | back off, pick another entity |
| `undetermined` | `sqlite3.OperationalError` containing `locked` / `busy`; **nothing is known about who won** | retry after `retry_after_ms`; do NOT assume loss |

`undetermined` is not decoration. `04-research.md:106-117` measured 1391–1400
`OperationalError: database is locked` per 200 trials once `busy_timeout` is 0, and
`04-research.md:94-99` shows the slowest 8-worker trial at 104.8 ms against a 5000 ms budget that
`db.connect()` never states. A design that returns three outcomes raises the fourth at its callers.

**Deviation from CLAUDE.md, stated deliberately:** the project rule is "MCP tools let exceptions
propagate to FastMCP's built-in error handling." The claim tool deliberately catches
`OperationalError` and converts it to `outcome="undetermined"`. Rationale: the entire product of
this feature *is* the outcome vocabulary; letting the one contended case escape as a stack trace
defeats it. Every other exception (`KeyError` on a missing entity, `ValueError` on a bad id) still
propagates untouched.

### 2. `busy_timeout` becomes explicit — independent of which proposal wins

`db.py:492-503`, opened this run:

```python
def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the codebugs database."""
    path = _db_path(project_dir)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    ...
```

The 5000 ms busy timeout that the entire clean-loss contract rests on is `sqlite3.connect()`'s
`timeout=5.0` default and appears nowhere in the source. Add one line after the WAL pragma:

```python
    conn.execute("PRAGMA busy_timeout=5000")  # explicit: the claim contract depends on this
```

Behavior-neutral today; makes the dependency readable and greppable. I would land this as a
separate commit before the feature regardless of which proposal wins.

### 3. Millisecond clock for claim timestamps

Claim timestamps use SQLite's own clock inside the statement:

```sql
strftime('%Y-%m-%dT%H:%M:%fZ', 'now')   -- e.g. 2026-08-05T11:22:33.417Z
```

Sorts lexicographically alongside `utc_now()` output, is generated server-side so no round-trip
skew, and closes the same-second ambiguity in note 4 above. Residual: two claims from the *same*
holder inside the same millisecond report `claimed` twice instead of `claimed` then `already_mine`.
That degradation is benign — the holder is told it holds the entity, which is what criterion 2
actually requires ("is never told it lost").

### 4. Terminal-safe projection (Q5, and the mechanical half of C1)

The projection clause is never a bare assignment. It is:

```sql
status = CASE WHEN status IN (<kind.terminal…>) THEN status ELSE :busy_status END
```

with the terminal set expanded from `kind.terminal` as bound parameters — `types.py:36`
(`FINDING_TERMINAL`, 3 members) and `types.py:41` (`REQUIREMENT_TERMINAL`, 4 members). A claim can
therefore never resurrect a `fixed` finding into `in_progress`. Symmetrically, release restores
`claim_prev_status` **only if** the current status still equals `busy_status`; if anything moved it
since, release clears ownership and leaves the status alone. The mechanism (capturing pre-claim
status in the same guarded UPDATE) is executed and proven at `04-research.md:382-384`.

---

## Proposal 1 — **Declared Claim Columns**

### Core Idea

Ownership becomes a *declared property of an entity kind*, not a feature of a table. `EntityKind`
gains one optional descriptor field, `claim: ClaimSpec | None`. `entities.py` gains its first and
only write path, `EntityRef.claim()` / `.release()` / `.claim_state()`, whose SQL is generated
entirely from that descriptor. The physical storage is four additive columns per participating
table, but **no module ever writes those column names by hand** — the migration that creates them
and the SQL that reads and writes them are both derived from the same declaration. Adding entity
kind #3 is one `EntityKind(...)` entry with a `claim=ClaimSpec(...)`; the ALTER TABLE, the index,
the claim statement, the release statement, and the reverse query all materialize from it.

This is the design that most directly answers "make it read as a capability of the entity layer,"
because the capability *is* an entry in the entity layer's declaration table.

### How It Works

**Descriptor change** — `entities.py`, additive, defaults preserve today's behavior:

```python
@dataclass(frozen=True)
class ClaimSpec:
    """Declares that a kind participates in entity claiming."""
    busy_status: str | None          # status to project while held; None = no projection
    holder_col: str = "claim_holder"
    claimed_at_col: str = "claim_claimed_at"
    renewed_at_col: str = "claim_renewed_at"
    prev_status_col: str = "claim_prev_status"


@dataclass(frozen=True)
class EntityKind:
    name: str
    table: str
    id_pattern: re.Pattern[str]
    terminal: frozenset[str]
    sort_col: str
    result_key: str
    readable_cols: frozenset[str]
    claim: ClaimSpec | None = None          # <-- the only new field
    schema_module: str = ""                 # for migration ordering; see below
```

`ENTITY_KINDS` (`entities.py:36-55`) grows two keyword arguments and nothing else:

```python
EntityKind(name=t.ENTITY_FINDING, table="findings", ..., 
           claim=ClaimSpec(busy_status="in_progress"), schema_module="findings"),
EntityKind(name=t.ENTITY_REQUIREMENT, table="requirements", ...,
           claim=ClaimSpec(busy_status=None), schema_module="reqs"),
```

**Criterion 5 falls out of the declaration.** `busy_status=None` for requirements. No status
projection, no migration to `REQUIREMENT_STATUSES`, no special case in any function body. This is
not a dodge: `resolve_requirement_status` (`types.py:92-94`) passes `aliases=None` into `_resolve`
(`types.py:72-84`), which raises `ValueError` on anything outside `REQUIREMENT_STATUSES`. So
`reqs_query(status="in_progress")` **raises today** — there is no existing consumer to keep working,
and criterion 6 is vacuous for this kind. Claiming a requirement records ownership and reports
outcomes exactly like a finding; it simply doesn't move the status. Kind #3 chooses per-kind.

**Migration** — `entities.ensure_claim_schema(conn)`, generated from the descriptor:

```python
def ensure_claim_schema(conn: sqlite3.Connection) -> None:
    for kind in ENTITY_KINDS:
        if kind.claim is None:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({kind.table})")}  # identifier is a frozen constant
        for col in _claim_cols(kind):
            if col not in cols:
                conn.execute(f"ALTER TABLE {kind.table} ADD COLUMN {col} TEXT")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{kind.table}_claim_holder "
            f"ON {kind.table}({kind.claim.holder_col}) "
            f"WHERE {kind.claim.holder_col} IS NOT NULL"
        )
    conn.commit()
```

Every interpolated identifier comes from a frozen module constant, and every one of them is already
covered by the `_SAFE_IDENT` invariant test at `tests/test_entities.py:149-152` once the claim
column names are added to that test's sweep. The `PRAGMA table_info` existence probe copies
`reqs.ensure_schema`'s own idiom verbatim (`reqs.py:46-48`).

Note the migration shape: **`ALTER TABLE … ADD COLUMN` only.** No table rebuild. Compare what the
alternative would have cost — `findings._migrate_statuses` (`findings.py:51-97`) and
`reqs._migrate_to_lowercase` (`reqs.py:53-95`) each rebuild the whole table, copy every row, drop,
rename, and hand-recreate four or five indexes, because they are widening a CHECK constraint. Adding
`in_progress` to `REQUIREMENT_STATUSES` would need exactly that. Declaring `busy_status=None`
costs zero rows moved. The partial-index form follows the shipped precedent at
`src/codebugs/milestones/_schema.py:77` (`idx_mi_assigned … WHERE assigned_agent IS NOT NULL`).

**Registration** — `entities.py` acquires a registration footprint for the first time:

```python
from codebugs.db import register_schema
register_schema(
    "entity_claims",
    ensure_claim_schema,
    depends_on=tuple({k.schema_module for k in ENTITY_KINDS if k.claim}),
)
```

`register_schema` accepts `depends_on` (`db.py:49-54`, per the legwork read) and `_resolve_order`
topologically sorts, so the ALTERs run strictly after `findings.ensure_schema` and
`reqs.ensure_schema` have created their tables. `depends_on` is **derived from the declaration**, so
kind #3 extends it automatically. `entities` is added to the import list in
`db._ensure_modules_loaded()` (`db.py:487`) — it is already imported transitively by
`findings.py:12` and `reqs.py:11`, so this is a formality.

**The claim path — exactly one statement:**

```sql
UPDATE findings
   SET claim_holder      = :holder,
       claim_renewed_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
       claim_claimed_at  = COALESCE(claim_claimed_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
       claim_prev_status = CASE WHEN claim_holder IS NULL THEN status ELSE claim_prev_status END,
       status            = CASE WHEN status IN (:t0,:t1,:t2) THEN status ELSE :busy_status END,
       updated_at        = :now
 WHERE id = :entity_id
   AND (claim_holder IS NULL OR claim_holder = :holder)
RETURNING claim_holder, claim_claimed_at, claim_renewed_at, claim_prev_status, status;
```

The table name, the four column names, the terminal-set arity and the `status = …` clause (dropped
entirely when `busy_status is None`) are all rendered from `kind` + `kind.claim`. Every *value* is a
bound parameter. All right-hand sides evaluate against the pre-update row, which is what makes the
`COALESCE` and both `CASE`s see the old holder and old status.

**Transaction shape: none.** One statement, one table, autocommit under `isolation_level=''`. This
is `04-research.md:214-217`'s executed result — a guarded `UPDATE` with no explicit transaction gave
exactly one winner in 200/200 trials at 2, 4 and 8 workers. And per `04-research.md:243`, we must
**never** write a plain `BEGIN`; we don't write one at all, so the `SQLITE_BUSY_SNAPSHOT` trap in
`04-research.md:203-208` is structurally unreachable.

**Outcome derivation:**

```python
def claim(self, conn, *, holder: str, steal: bool = False) -> dict[str, Any]:
    ...
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            return {"outcome": "undetermined", "entity_id": self.id, "retry_after_ms": 250,
                    "detail": str(e)}
        raise
    conn.commit()
    if row is not None:
        outcome = "claimed" if row["claim_claimed_at"] == row["claim_renewed_at"] else "already_mine"
        return {"outcome": outcome, "entity_id": self.id, "holder": holder, ...}
    # no row: either held by someone else, or the entity does not exist
    self.require(conn)                      # entities.py:105-108 — raises KeyError if absent
    return {"outcome": "held_by_other", "entity_id": self.id, **self.claim_state(conn)}
```

`self.require(conn)` is the existing method at `entities.py:105-108`; reusing it means a claim on a
nonexistent id raises `KeyError` exactly like every other domain function (CLAUDE.md error rule) and
never masquerades as a lost race.

**Reverse query (criterion 3):** `claim_state(conn)` for "who holds CB-1234" is a single indexed
point read. "What does agent-7 hold" is one indexed `SELECT` per claiming kind — two statements
today, N with N kinds, each hitting `idx_findings_claim_holder` / `idx_requirements_claim_holder`,
merged in Python and keyed by `kind.result_key` (`entities.py:43`, `:52`) so the envelope shape
matches every other multi-kind response in the codebase. Contrast `04-research.md:436-442`, where
the same question against an append-only log is a **752 ms full window fold at 500k rows that no
index improves**. Mine is a bounded index seek and stays one.

**Staleness (criterion 7):** `claim_renewed_at` plus a *reader-chosen* threshold —
`claims_list(stale_after_minutes=90)` filters `renewed_at < :cutoff`. No stored TTL, no sweeper, no
background process, matching C8 and the user's "report, never auto-steal." `steal=True` widens the
guard to `AND (claim_holder IS NULL OR claim_holder = :holder OR claim_renewed_at < :cutoff)` and is
never the default.

**C1 — what happens when `provenance.py:265-270` fires on a claimed entity.**

Concretely: agent-a claims CB-1234 → `claim_holder='agent-a'`, `claim_prev_status='open'`,
`status='in_progress'`. Agent-a commits `Resolves: CB-1234`. Someone runs
`codebugs resolve-trailers`. `resolve_trailers` reads the finding (`provenance.py:257`), sees
`in_progress` is not in `FINDING_TERMINAL` so it does not skip (`provenance.py:261-263`), and calls
`findings.update_finding(status='fixed', append_note=...)` (`provenance.py:265-270`), which lands
`UPDATE findings SET status = ?, meta = ?, updated_at = ? WHERE id = ?` at `findings.py:298`.

**Result under Proposal 1: `status` becomes `fixed`; `claim_holder` stays `'agent-a'`. Nothing
breaks, nothing is lost, and no code in `provenance.py` changes.**

I want to defend that as *correct* rather than tolerated. The row now says: agent-a claimed this,
agent-a fixed it, agent-a never released it. That is a true and useful statement. The claim columns
are not a lock whose invariant the provenance writer violates — they are a record of who touched the
entity, and the provenance write is that same agent's own work landing. The three consequences,
each handled:

1. **A later claim by agent-b is refused** (`claim_holder` is non-NULL and ≠ `agent-b`) — correct;
   agent-b is told `held_by_other` and should look at a `fixed` finding and move on.
2. **Re-claim by agent-a returns `already_mine`** and, thanks to the terminal-safe `CASE` in §4,
   does **not** resurrect `fixed` → `in_progress`.
3. **`release()` after the fact** sees `status='fixed' != busy_status`, so it clears the four claim
   columns and leaves `fixed` untouched. Q5 answered, with the pre-existing status writer as the
   worked example rather than as a hypothetical.

For visibility, `claims_list()` reports any row with a holder whose status is in `kind.terminal` as
`state="completed_unreleased"` — a derived label, not stored state, computed from `terminal`, which
is already a declared field of every kind (`entities.py:41`, `:50`).

**Optional hardening, explicitly *not* required:** `findings.update_finding` could clear the claim
when the new status is terminal. I recommend against it in v1. It puts kind-specific ownership logic
back inside a domain module — precisely the coupling criterion 4 exists to prevent — to buy tidiness
over a state that is already correct and already reported.

**Q7 / C11 — the exact file and change that makes this get called.**

`/home/faxik/.claude/skills/fix-latest-codebugs/SKILL.md:92`, Phase 5 ("Lock"), verbatim today:

```
1. `mcp__codebugs__update(id="CB-1234", status="in_progress", assignee="claude")` — claims the bug.
```

This call **cannot succeed**. The MCP `update` tool signature is `update(finding_id, status, notes,
tags, meta_update, reported_at_ref)` — `findings.py:573-581`, opened this run. There is no `id`
parameter and no `assignee` parameter. FastMCP will reject the call on the unknown kwargs. The
documented claim path in the skill the user runs to work this very queue is broken right now.

Replace it with:

```
1. `mcp__codebugs__claim(entity_id="CB-1234", holder="claude")` — claims the bug.
   - `outcome: "claimed"` or `"already_mine"` → proceed.
   - `outcome: "held_by_other"` → report the holder and `claimed_at` to the user and offer the
     next candidate. Do not start work.
   - `outcome: "undetermined"` → wait `retry_after_ms` and call once more; if still undetermined,
     report and pick another candidate.
```

and add the release to the finishing phase. This is a strictly stronger adoption story than adding
an optional tool: it is not "please also call this," it is **repairing an instruction that is
already failing**, on the hot path of a skill the user invokes by name. Contrast the counterexample
the brief flags (fact 2): `pull_next` is correct, shipped, and unwired because nothing was ever
broken enough to force its adoption.

*Second, weaker lever, offered as optional:* `update_finding` (`findings.py:235-302`) is the single
choke point through which every status write in the codebase passes. Adding an additive
`"claim_conflict": {...}` key to its return dict when a busy-status write targets an
other-held entity is non-breaking and makes the legacy path self-documenting. I'd defer it; the
skill edit is the load-bearing one.

**Criterion 8 — `milestone_items` convergence.** Not subsumed, converged, and the two keep distinct
meanings: `milestone_items.assigned_agent` (`milestones/_schema.py:64`, opened this run) means "who
pulled this scheduled work item, under capacity accounting"; the entity claim means "who is touching
this entity right now." The convergence step is one call: `pull_next` (which the brief places at
`capacity.py:167-215` — **I did not open that file this run; the implementer must re-verify the
range**) calls `entities.EntityRef.of(item_ref).claim(conn, holder=agent_id)` for
`item_kind in ('bug','requirement')` — both already legal values of the `item_kind` CHECK at
`milestones/_schema.py:55-56` — inside its existing `BEGIN IMMEDIATE`, and `release_item` calls
`.release()`. After that they cannot diverge, because one writes the other. `item_kind='external'`
has no `EntityRef` and is skipped. This also, incidentally, finally wires `pull_next` into something.

**Criterion 1 — the test.** Follow `tests/test_milestones.py:801` (per `04-research.md:659-664`):
two threads, each its own `db.connect(tmp_project)` against a file-backed DB, `threading.Barrier(2)`,
assert exactly one `claimed`. Add a second test at `busy_timeout=0` asserting the loser gets
`outcome="undetermined"` and never an escaping `OperationalError` — that is the only way the fourth
outcome gets covered, and `04-research.md:668` notes no test in the repo touches `busy_timeout` today.

### Pros

- **Claim and projection are one statement on one row.** No transaction ceremony, no cross-table
  window, no `BEGIN` of any kind, so the `SQLITE_BUSY_SNAPSHOT` trap is unreachable by construction.
- **Criterion 4 is met in its strong form.** Kind #3 = one `EntityKind(...)` entry. Migration,
  index, claim SQL, release SQL, and reverse query are all generated from it. Zero new ownership code.
- **Criterion 5 is met by declaration, not by exception.** `busy_status=None` costs one keyword and
  avoids a full `requirements` table rebuild.
- **Criterion 3's expensive direction is cheap** — indexed seek per kind, versus the measured 752 ms
  window fold an append-only substrate pays (`04-research.md:439-442`).
- **The cheapest possible migration shape** — `ALTER TABLE ADD COLUMN`, no rows moved, versus the
  two table-rebuild migrations already in this repo.
- `PRAGMA foreign_keys=0` (`04-research.md:262`) is a non-issue: ownership is *in* the row, so it
  cannot be orphaned, and deleting the entity deletes the claim atomically and for free. Every
  design that stores ownership elsewhere inherits an orphan-cleanup obligation that this one does
  not have.
- Reuses `EntityRef.require` (`entities.py:105-108`) so missing-entity behavior matches the rest of
  the codebase without new error handling.
- Claim column identifiers automatically fall under the existing `_SAFE_IDENT` invariant test
  (`tests/test_entities.py:149-152`).

### Cons — honest

- **This is the closest of my three to the design the user already rejected.** The user called
  `assigned_agent` / `claimed_at` on `findings` "прибито гвоздями." My defense is that the rejected
  design bolted findings-specific columns onto a findings-specific table from findings-specific
  code, whereas here the columns exist *because a kind declared it* and no module names them. That
  is a real difference in character. **But it is a difference in character, not in physical schema
  — the `findings` table does end up with `claim_holder`.** If the objection is literally "no
  ownership columns on `findings`," Proposal 1 is dead on arrival and Proposal 2 is next.
- **`entities.py` stops being read-only** (Q2). Its docstring at `entities.py:1-8` says it owns "the
  one sanctioned cross-table read." Widening that charter to a cross-table *write* is a genuine
  architectural change, and worse, `ensure_claim_schema` ALTERs tables owned by `findings` and
  `reqs` — a cross-module schema reach that CLAUDE.md's "no module should reach into another
  module's tables directly" rule points away from. My argument is that `entities.py` is exactly the
  place the codebase already chartered for sanctioned cross-table access, and centralizing it in
  one declaration-driven function is better than three modules each writing their own claim code.
  **This is the strongest attack available against Proposal 1 and I expect the adversary to take
  it.**
- **Four columns × N kinds is real schema surface.** Wide tables, and every kind pays even if it
  never uses claims (mitigated by `claim=None`).
- **No history.** The row holds only the current claim. Who held CB-1234 last week is unrecoverable.
  `milestone_audit` exists and could receive claim events, but writing to another module's table
  from `entities.py` is a worse violation than the schema one above, so I do not propose it.
- **`entities` acquiring a `register_schema` call adds a fourth registration site** to a codebase
  that has been consolidating them (ARCH-001/002/004), and `depends_on` ordering becomes
  load-bearing — if a future kind forgets `schema_module`, its ALTER runs before its `CREATE TABLE`.
- Same-millisecond same-holder re-claim reports `claimed` instead of `already_mine` (benign, §3).

### Effort Estimate

**M.** ~120 lines in `entities.py` (`ClaimSpec`, `ensure_claim_schema`, `claim`, `release`,
`claim_state`, `claims_list`, SQL rendering), one keyword on each of two `EntityKind` entries, one
line in `db._ensure_modules_loaded()` (`db.py:487`), one line in `db.connect()`, ~5 MCP tools and
their CLI mirrors, one 15-line call added to `pull_next`, ~200 lines of tests including the
two-connection race and the `busy_timeout=0` case, and the `SKILL.md:92` edit. No table rebuilds, no
data migration, no backfill.

### Risk Profile

- **Correctness: low.** Single guarded statement — the substrate proven at `04-research.md:214-217`
  with the fewest moving parts of the four tested.
- **Migration: low.** `ADD COLUMN` is non-destructive and idempotent behind the `PRAGMA table_info`
  probe. Rollback = drop the index and ignore the columns.
- **Architectural: medium-high.** Turning `entities.py` into a writer that ALTERs other modules'
  tables is the one decision here that a reviewer could reasonably veto.
- **Adoption: low.** Attached to a broken instruction on a hot path.
- **Acceptance: medium-high.** The user has already rejected a shape that looks like this from the
  outside.

---

## Proposal 2 — **Meta Claim Envelope**

### Core Idea

Zero DDL. Both participating tables already carry a JSON object column —
`findings.meta TEXT NOT NULL DEFAULT '{}'` (`findings.py:26`) and
`requirements.meta TEXT NOT NULL DEFAULT '{}'` (`reqs.py:27`) — and SQLite 3.47.1
(`04-research.md:61`) has the full JSON1 mutator set. Ownership becomes a reserved
`meta.claim` sub-object, `{holder, claimed_at, renewed_at, prev_status}`, written and guarded by a
single `UPDATE … SET meta = json_set(...) WHERE json_extract(meta, '$.claim.holder') IS NULL OR = ?`.
No migration at all: every existing row already has a valid empty envelope by the column default.
The claim becomes a *convention* over an existing generic slot rather than a schema change.

### How It Works

**Schema change: none.** Not "additive" — *none*. Every findings row and every requirements row in
every existing database is already claim-ready, because `'{}'` is a valid claim-free envelope.

`EntityKind` still gains `claim: ClaimSpec | None`, but `ClaimSpec` carries a JSON path prefix
instead of column names:

```python
@dataclass(frozen=True)
class ClaimSpec:
    busy_status: str | None
    meta_col: str = "meta"
    path: str = "$.claim"
```

**Claim path — one statement:**

```sql
UPDATE findings
   SET meta = json_set(
                json_set(
                  json_set(
                    json_set(meta,
                      '$.claim.holder',     :holder),
                      '$.claim.claimed_at', COALESCE(json_extract(meta,'$.claim.claimed_at'),
                                                     strftime('%Y-%m-%dT%H:%M:%fZ','now'))),
                      '$.claim.renewed_at', strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                      '$.claim.prev_status', CASE WHEN json_extract(meta,'$.claim.holder') IS NULL
                                                  THEN status
                                                  ELSE json_extract(meta,'$.claim.prev_status') END),
       status     = CASE WHEN status IN (:t0,:t1,:t2) THEN status ELSE :busy_status END,
       updated_at = :now
 WHERE id = :entity_id
   AND (json_extract(meta,'$.claim.holder') IS NULL
        OR json_extract(meta,'$.claim.holder') = :holder)
RETURNING json_extract(meta,'$.claim.claimed_at'),
          json_extract(meta,'$.claim.renewed_at');
```

Same four-outcome derivation, same terminal-safe projection, same no-transaction shape as
Proposal 1 — the guard is in the `WHERE`, which is the part `04-research.md:214-217` proved
sufficient.

**Indexing.** SQLite supports expression indexes, so the reverse query is not condemned to a scan:

```sql
CREATE INDEX IF NOT EXISTS idx_findings_claim_holder
    ON findings(json_extract(meta,'$.claim.holder'))
 WHERE json_extract(meta,'$.claim.holder') IS NOT NULL;
```

Honesty flag: this is the one mechanic in this document I have **not** seen executed, either by me
or in `04-research.md`. Expression-index usage requires the query's expression to match the index's
textually. It must be `EXPLAIN QUERY PLAN`-verified before this proposal is costed as cheap.

**The genuinely delightful property.** "What does agent-7 hold" **already works for findings with
zero new code**, because `query_findings` builds exactly this predicate today —
`findings.py:365-368`, opened this run:

```python
    if meta_key and meta_value:
        conditions.append("json_extract(meta, ?) = ?")
        params.append(f"$.{meta_key}")
        params.append(meta_value)
```

So `query(meta_key="claim.holder", meta_value="agent-7")` is a working reverse-ownership query
against a tool that shipped before this design existed. Nothing else in this council can claim that.

**C1 — provenance.** Better than Proposal 1 in one specific respect and worse in another.
`provenance.py:265-270` calls `update_finding(status='fixed', append_note=...)`, and `append_note`
takes the `findings.py:270-275` branch: `json.loads(row["meta"])`, mutate `notes`, `json.dumps`,
`meta = ?`. **The claim survives** — the round-trip preserves unknown keys, so `meta.claim` comes
back intact, and the outcome is identical to Proposal 1: `status='fixed'`, claim still held,
reported as `completed_unreleased`. **But it survives by luck**, not by design: it survives because
that particular code path happens to preserve unrecognized keys. That distinction is the whole con
section below.

**Q7 / C11:** identical to Proposal 1 — `SKILL.md:92`.

**Criterion 5:** identical — `busy_status=None`.

**Criterion 8:** identical convergence via `pull_next`.

### Pros

- **Zero DDL, zero migration, zero backfill, zero rollback risk.** Nothing to un-ship.
- **Zero new schema surface.** No wide tables, no per-kind columns, and a kind that never claims
  costs literally nothing.
- **Criterion 4 in its strongest possible form:** kind #3 needs not even an ALTER TABLE — only a
  `meta` column and a declaration. Every entity table in this codebase already has one.
- The findings reverse query is **already shipped** (`findings.py:365-371`).
- Same one-statement, no-ceremony claim+projection as Proposal 1.
- Deleting an entity deletes its claim atomically; no orphans despite `foreign_keys=0`.

### Cons — honest, and one of them is close to disqualifying

- **The lost-update hazard is real and it is in the shipped code.** `update_finding` mutates `meta`
  by **Python-side read-modify-write**: `json.loads(row["meta"])` at `findings.py:265`, `:271`,
  `:282`, then `params.append(json.dumps(existing_meta))`, then one `UPDATE … SET meta = ?` at
  `findings.py:298`. The row was read at `findings.py:252`. **Any concurrent `update(meta_update=…)`
  overlapping a claim silently erases the claim** — it writes back a whole `meta` blob computed from
  a snapshot taken before the claim landed. This is not a race the claim statement can win, because
  the claim is not what's racing; the *other* writer clobbers it wholesale. Proposal 1 is immune
  (separate columns, and `update_finding` never names them). Fixing it means converting all three
  `meta` branches in `update_finding` — and the matching ones in `reqs.update_requirement` — to
  SQL-side `json_patch`/`json_set`, which is a worthwhile change on its own merits but is
  substantially more invasive than the four `ALTER TABLE`s that Proposal 1 costs. **A design whose
  headline is "zero migration" that requires rewriting two modules' meta handling to be safe has
  lost its headline.**
- **Requirements get a second-class reverse query.** `query_requirements` (`reqs.py:235-249`,
  opened this run) has **no** `meta_key` / `meta_value` filter. So "what does agent-7 hold" is free
  for findings and needs new code for requirements — the exact asymmetry criterion 4 forbids. Fixing
  it means adding meta filtering to `reqs.py`, which is fine but is again new per-kind code.
- **`meta` is an open namespace with no reservations.** `add_finding` (`findings.py:133-145`) accepts
  any dict; `meta_update` merges any keys. Nothing stops a caller from writing `meta.claim` by hand
  and forging or destroying ownership through the ordinary `update` tool. Ownership becomes
  advisory-by-convention in a way that columns are not.
- **`update_finding` already has a latent multi-`meta = ?` bug** — passing `notes` and `meta_update`
  together appends two `meta = ?` assignments (`findings.py:267`, `:284`) built from the same
  pre-read row, and the later silently wins. Claims layered into that column inherit it.
- The expression-index path is unverified (flagged above).
- Nested `json_set` calls are genuinely hard to read and review compared to four named columns.

### Effort Estimate

**S** if you accept the lost-update hazard and the requirements query asymmetry.
**L** if you fix them — which I think you must, making the honest number **L**.

### Risk Profile

- **Correctness of the claim statement itself: low** (same guarded-WHERE substrate).
- **Correctness of the claim's *survival*: high.** The clobber path is live code today, not a
  hypothetical.
- **Migration: none.** The single best score in this document.
- **Architectural: low-medium.** No charter change to `entities.py`'s schema ownership; it still
  needs a write path, but it never ALTERs anything.
- **Reviewability: medium-low.** Nested `json_set` is hostile to review.

---

## Proposal 3 — **Milestone Spine Claim**

### Core Idea

Do not invent ownership; **route to the ownership that already exists.** `milestone_items` has
`assigned_agent`, `pulled_at`, `done_at`, `done_commit` and a partial index on the holder
(`milestones/_schema.py:64-67`, `:77`, opened this run), and its `item_kind` CHECK already admits
`'bug'` and `'requirement'` (`milestones/_schema.py:55-56`) with `item_ref` holding the entity id.
Findings are *already* auto-routed into `stream/triage` on creation by the post-add hook (CLAUDE.md;
mechanism at `db.register_post_add_hook`, `db.py:178-190`). So a claim on CB-1234 becomes a guarded
UPDATE of that entity's existing `milestone_items` row, plus a status projection onto the entity.
This is the only one of my three that answers criterion 8 by **elimination** — there is no second
representation of ownership, because there is no new one.

### How It Works

- Extend the post-add hook pattern to requirements so every claimable entity has a spine row
  (findings already do).
- **Claim** — two tables, therefore multi-statement, therefore `BEGIN IMMEDIATE` per
  `04-research.md:239-241`, following the save/restore-`isolation_level` pattern the brief cites at
  `capacity.py:167-215` (**not opened by me this run**):

```sql
-- statement 1, the guard
UPDATE milestone_items
   SET assigned_agent = :holder,
       pulled_at      = COALESCE(pulled_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
       status         = CASE WHEN status IN ('done','dismissed') THEN status ELSE 'in_progress' END,
       updated_at     = :now
 WHERE item_ref = :entity_id
   AND item_kind = :kind
   AND (assigned_agent IS NULL OR assigned_agent = :holder)
RETURNING id, milestone_id, assigned_agent, pulled_at;

-- statement 2, the projection (skipped when kind.busy_status is None)
UPDATE findings
   SET status = CASE WHEN status IN (:t0,:t1,:t2) THEN status ELSE :busy_status END,
       updated_at = :now
 WHERE id = :entity_id;

-- statement 3, the audit row — milestone_audit already exists (milestones/_schema.py:79-85)
INSERT INTO milestone_audit (milestone_id, item_ref, actor, action, from_state, ...) VALUES (...);
```

- Outcome vocabulary identical, including `undetermined` — and this design *needs* it most, because
  `BEGIN IMMEDIATE` can itself raise `database is locked` before any guard is evaluated
  (`04-research.md:152-158`).
- Reverse query is already indexed: `idx_mi_assigned` (`milestones/_schema.py:77`).
- History is free: `milestone_audit` already exists and is already the audit sink for this domain.
- `pull_next` needs no wiring at all — it *is* the claim, one layer down.

### Pros

- **Criterion 8 is answered by construction.** One ownership store, forever. No convergence plan
  needed because there is no divergence.
- **Free audit history** in a table that already exists and already means this.
- **Free reverse-query index** and free capacity semantics.
- Zero new columns anywhere; the most literal reading of my lever.
- Genuinely re-frames ownership as workflow, which is what the user said the problem "looks like."

### Cons — honest, and I consider them fatal for v1

- **An entity can have rows in more than one milestone.** `UNIQUE(milestone_id, item_kind, item_ref)`
  (`milestones/_schema.py:72`) is scoped *per milestone*, so CB-1234 can legally sit in
  `stream/triage` **and** `release/1.1`. "Who holds CB-1234" then has two answers, and the claim
  UPDATE above (`WHERE item_ref = :entity_id`) would claim **all** of them, or arbitrarily one.
  There is no designated canonical row, and inventing one means either a new column (forbidden by my
  lever) or a convention that a reviewer will correctly call fragile. **This alone sinks it.**
- **`entities.py` would have to know about the milestones domain**, importing a domain module into
  the one module whose entire value is that it imports only `types` (`entities.py:4-7`). Or the
  claim lives in `milestones/`, at which point ownership is nailed to the milestones domain — the
  same "прибито гвоздями" objection the user raised, relocated.
- **Claim + projection is a two-table write**, so it needs `BEGIN IMMEDIATE`, a divergence window,
  and the isolation-level save/restore dance the researcher already flagged as fragile and
  copy-pasted (`04-research.md:304-306`). Proposals 1 and 2 have none of this.
- **It drags capacity accounting, `size` gating, and `linked_frs` validation into what should be a
  two-field concept.** Criterion 4's "third entity kind needs no new ownership code" becomes "third
  entity kind needs a milestone routing rule, an `item_kind` CHECK migration (a table rebuild), and
  a capacity policy."
- Requirements would need a new auto-routing hook to have a spine row at all.
- **C1 gets worse, not better.** `provenance.py:265-270` moves `findings.status` to `fixed` while
  `milestone_items.status` stays `in_progress` and `assigned_agent` stays set — now the divergence
  is *across two tables* and needs an explicit reconciler, where Proposals 1 and 2 keep it in one
  row that is simply, visibly, "held and terminal."
- Claims become inseparable from milestone semantics: you cannot claim an entity that isn't in a
  milestone, which makes claiming conditional on unrelated bookkeeping.

### Effort Estimate

**L**, and **XL** if the multi-milestone ambiguity is solved properly rather than papered over.

### Risk Profile

- **Correctness: medium.** Multi-statement, cross-table, `BEGIN IMMEDIATE`-dependent, and with a
  genuine ambiguity in *which row* is the claim.
- **Architectural: high.** Forces either a domain import into `entities.py` or the relocation of the
  whole capability into `milestones/`.
- **Scope: high.** Couples a two-field concept to release planning and capacity accounting.
- **Acceptance: high risk.** Most likely to reproduce the "nailed down" objection in a new location.

---

## Recommendation

**Proposal 1 — Declared Claim Columns.**

Ranked: **1 > 2 > 3**, and the gap between 1 and 2 is much smaller than between 2 and 3.

**Why 1 over 3.** Proposal 3 is the most *conceptually* satisfying — one ownership store, free
audit, free index — and it dies on a schema fact I verified this run: `UNIQUE(milestone_id,
item_kind, item_ref)` at `milestones/_schema.py:72` is per-milestone, so an entity can hold multiple
spine rows and "who holds CB-1234" has no single answer. Every fix for that needs either a new
column (forbidden to me) or a convention that will not survive review. It also converts a
one-statement claim into a two-table transaction and drags capacity semantics into a two-field
concept. Rejected.

**Why 1 over 2.** Proposal 2 has the better headline — zero migration, and a reverse query that
already ships at `findings.py:365-371`. I nearly recommended it. What stopped me is a shipped code
path, not a hypothetical: `update_finding` rebuilds the entire `meta` blob in Python from a row read
at `findings.py:252` and writes it back whole at `findings.py:298` (`findings.py:265`, `:271`,
`:282`). **Any concurrent `meta_update` silently erases a claim stored in that column**, and the
claim statement cannot defend itself because it is not the statement that's racing. Making
Proposal 2 safe means rewriting `meta` handling in both `findings.py` and `reqs.py` to SQL-side
`json_patch` — which costs more than the four `ALTER TABLE ADD COLUMN`s that Proposal 1 costs, and
destroys the zero-migration headline that was Proposal 2's whole case. Add the missing `meta_key`
filter on `query_requirements` (`reqs.py:235-249`) and the open-namespace forgeability of `meta`,
and the cheap option is only cheap if you don't finish it.

**Why 1 on its own merits, against C7's honest bar.** C7 says this is API expressiveness, not data
integrity, and that a proposal adding a subsystem must clear that lower bar. Proposal 1 **does not
add a subsystem.** It adds one optional field to a frozen dataclass that already exists for exactly
this purpose — declaring per-kind capabilities — plus four nullable columns created by the cheapest
migration SQLite offers, plus one write method on the module the codebase already chartered for
cross-table access. There is no new table, no new module, no new registry, no new transaction
pattern, and no background process. Weighed against a problem whose observed cost is duplicated work
rather than corruption (brief, hypotheses §1), that is roughly the right amount of machinery — and
notably less than a market leader has managed, given Jira has carried this exact race as
JRASERVER-78379 for years (C12).

**What would change my mind.** If the user's objection to the first design was literally *"nothing
new on the `findings` table"* rather than *"stop patching findings specifically,"* then Proposal 1
is dead regardless of how well-declared its columns are, and Proposal 2 becomes the recommendation —
with the `update_finding` / `update_requirement` `meta` rewrite budgeted in from day one rather than
discovered later. That is the single question I would put to the user before implementation starts.

**Land in this order, regardless of which proposal wins:**

1. `PRAGMA busy_timeout=5000` made explicit in `db.connect()` (`db.py:492-503`) — behavior-neutral,
   makes the contract's dependency readable.
2. The claim capability itself.
3. `/home/faxik/.claude/skills/fix-latest-codebugs/SKILL.md:92` — replace the call that cannot
   succeed today. Without this step the feature is `pull_next` again.
