# Adversarial Review — Round 1

**Evidence discipline.** Every `file:line` below was opened this run, by me or by a delegated
reader whose quotes I spot-checked. Every runtime claim was **executed** against this repo's own
interpreter (`uv run python`, sqlite 3.47.1). My probe is
`/tmp/claude-1000/-home-faxik-w-codebugs/349b1511-db4b-4ac3-a327-b85cb19885ba/scratchpad/adv_probe.py`
and its full output is reproduced inline where it is load-bearing. Where I am relying on a
document rather than my own read, I say so.

---

## 0. Three results that reframe the whole council

I am putting these first because they change how every proposal below should be scored, and
because **not one of the nine proposals contains them.**

### 0.1 The race has fired in production. Twice. With a written post-mortem. — THE BRIEF IS REFUTED

The brief lists as its first hypothesis: *"[hypothesis — never observed] That the race has actually
fired in production… No trace, log, or incident has been produced. A design whose value proposition
requires the race to be frequent is unsupported."* (`00-problem-brief.md:129-133`)

All three architects accepted that and discounted their own work against it — B: *"a problem the
brief itself notes has never been observed firing"* (`02:912`); C: *"the race has never been observed
firing"* (`03:994`); A: *"a problem whose observed cost is duplicated work rather than corruption"*
(`01:763`).

It is false. `/home/faxik/w/autosorter/tools/worktree-setup.sh:58-66`, opened this run:

```bash
# The check above matches an exact worktree PATH, so two different slugs for the
# SAME card pass it. That is not hypothetical: `fix-cb-2534-debug-rescue-scope`
# and `fix-cb-2534-2417-documents-router-scope` were built in parallel on
# 2026-08-04, and CB-2431 before them for ~40 minutes. Both times the card was
# already `in_progress` — but that is a WRITE-ONLY field, read by nothing, so it
# stopped no one.
```

Two named incidents, dated, one costed at ~40 minutes. The council was convened on the premise that
the harm is speculative; the harm is documented in the repository next door.

### 0.2 The same post-mortem states a conclusion that invalidates the premise of all nine proposals

`/home/faxik/w/autosorter/tools/CLAUDE.md:10`, verbatim:

> "…that is how CB-2431 (~40 min) and CB-2534 were each built twice, **both times while the card was
> already `in_progress`**. `status=in_progress` is a **WRITE-ONLY field that nothing reads, so it
> never stopped anyone**; the branch-name check has teeth because it is pure git and precedes the
> irreversible act."

Read that against what all nine proposals do. Every one of them improves what a **claim write**
reports to its caller. In both observed incidents **the claim write was already correct** — the card
said `in_progress` — and the collision happened anyway, because the failure was at the **read**.

**No proposal in this council would have prevented CB-2431 or CB-2534.** That is not a rhetorical
flourish; it follows directly from the incident description. A `held_by_other` outcome only helps an
agent that calls claim and then obeys the answer. In both incidents one side never consulted the
tracker at all.

The user has already diagnosed this, already chosen a layer, and already shipped: the fix is a git
branch-name guard (`worktree-setup.sh:75-105`) that runs *before the irreversible act*, with
`--allow-duplicate` as the explicit override. The council is designing a better version of the
mechanism its own domain expert wrote off as toothless.

### 0.3 There is a LIVE claim call site. All nine proposals target a documentation line instead.

Every proposal names `~/.claude/skills/fix-latest-codebugs/SKILL.md:92` as *the* adoption lever
(A `01:349-377`, B `02:847-863`, C `03:1058-1079`), each arguing that repairing a broken instruction
beats adding an optional tool. The instruction is indeed broken — verified: the MCP tool signature at
`findings.py:573-579` is `update(finding_id, status, notes, tags, meta_update, reported_at_ref)`,
with no `id` and no `assignee`.

But it is a *documentation* line. The **working** claim is here —
`/home/faxik/w/autosorter/tools/worktree-setup.sh:208-214`, opened this run:

```bash
for cb in ${_claim_ids}; do
    if codebugs update "${cb}" --status in_progress >/dev/null 2>&1; then
        echo "  ✓ ${cb} → in_progress (claimed by ${BRANCH_NAME})"
```

Three things follow, and each is fatal to an adoption plan that does not mention it:

1. **It goes through the CLI**, not MCP — `findings.py:749` `_cmd_update` → `update_finding` →
   `findings.py:298`. Every proposal specifies MCP tools plus CLI mirrors for its *new* surface; none
   changes the CLI path that is actually carrying the traffic.
2. **It passes no agent identity.** Every proposed API is `claim(entity_id, holder=…)` /
   `claim(entity_id, actor=…)`. At the only live call site there is nothing to pass. An identity
   scheme is a prerequisite nobody designed.
3. **It already implements a read-then-claim by hand**, `worktree-setup.sh:180-195`: it shells
   `codebugs get`, parses `status` out of the JSON with `sed`, and claims only on `open`. That is a
   textbook check-then-act race, in shell, unguarded — and it is the exact race this council exists
   to close, sitting in the one place none of the nine proposals looked.

The brief flagged this as untraced (*"[hypothesis — control-flow not traced] That direct
`update(status="in_progress")` is the only live claim path"*, `00:134-136`). All three architects
treated it as traced. It is now traced, and the answer is not the one they assumed.

---

## Cross-cutting findings (scored against every proposal that inherits them)

### X-1 [FATAL for A1/B1/B3 as specified, SERIOUS for C1/C2] — the `pull_next` wiring is a transaction bomb

Criterion 8 is answered by all three architects the same way: call the new claim primitive from
inside `pull_next`'s existing transaction. A `01:388-394`; B `02:788-795` (*"one line in an existing
transaction"*); C `03:1046-1050`.

`pull_next` (`capacity.py:179-218`, opened this run) runs its **entire body** inside an explicit
transaction and rolls back on any exception:

```python
179	    saved_isolation = conn.isolation_level
180	    conn.isolation_level = None
181	    try:
182	        conn.execute("BEGIN IMMEDIATE")
...
213	            conn.execute("COMMIT")
214	        except Exception:
215	            conn.execute("ROLLBACK")
216	            raise
```

Executed (P2):

```
nested BEGIN IMMEDIATE -> OperationalError: cannot start a transaction within a transaction
after inner conn.commit(): in_transaction=False
visible to an INDEPENDENT connection: [('outer-partial',)]   <-- outer partial work LEAKED
outer ROLLBACK -> OperationalError: cannot rollback - no transaction is active
final rows visible externally: [('outer-partial',)]
```

Per proposal:

- **A1 — silent partial-write leak.** A's `claim()` calls `conn.commit()` (`01:287`). Dropped into
  `pull_next`, that commits the item `UPDATE` (`capacity.py:196-201`) and the capacity increment
  (`:202`) **before** the audit insert (`:203-212`). If anything later raises, `capacity.py:215`'s
  `ROLLBACK` itself raises `cannot rollback - no transaction is active`, masking the original
  exception, and the half-written pull is externally visible and unrecoverable. This destroys the
  exact atomicity property `pull_next`'s `BEGIN IMMEDIATE` exists for and that
  `tests/test_milestones.py:801` tests.
- **B1/B3 — hard failure.** `claims.claim()` with `project=True` (the default, `02:312-313`) enters
  `db.immediate_txn` (`02:161-176`), whose first act is `conn.execute("BEGIN IMMEDIATE")` inside
  `pull_next`'s open transaction → `OperationalError: cannot start a transaction within a
  transaction`. `pull_next` is then permanently broken for every `item_kind IN ('bug','requirement')`
  — i.e. for everything except the one kind B excludes. B's proposed refactor of `capacity.py` onto
  `immediate_txn` (`02:179`) does not help; it makes the nesting symmetrical, not legal.
- **C1/C2 — unspecified, but the chosen precedent is the safe one.** C adds `claims.record(...)`
  beside `_audit(...)`. Verified: `_audit` (`milestones/_spine.py:64-80`) is a bare `conn.execute`
  INSERT with **no commit** — and a grep of `src/codebugs/milestones/` confirms `_spine.py` is the
  one file there containing no `conn.commit()` at all. So C picked the one seam in `pull_next` whose
  local convention is "append, let the caller commit." If `record()` follows it, C is correct and
  this is a non-issue for C. C never states it, and C's *other* seam (`provenance.py`, `03:412-421`)
  is explicitly described as a **separate** transaction — so a reader has no basis to assume the safe
  answer. C is exposed to an unforced error, not to a defect.

**A smaller variant hits the `release_item` half of the same convergence plan.** A wires `release()`
into `release_item` (`01:393`); B's plan covers it too. Verified: `release_item`
(`capacity.py:223-275`) uses **no** `BEGIN IMMEDIATE` but does `conn.commit()` at `:274` after three
writes (the item `UPDATE`, `_decrement_capacity`, `_audit`). A `release()` that commits partway
through commits the item update without the capacity decrement. No exception is raised — the outer
`commit()` at `:274` simply becomes a second commit — so this one fails **silently**, which is
worse than B1's loud version.

**The fix is not hard** (a `manage_txn: bool = False` parameter, or splitting the primitive into a
statement-emitting core and a transaction-managing wrapper). The finding is that three architects,
under three opposed levers, all wrote "one line in the existing transaction" and none of them opened
`capacity.py:179-218` closely enough to notice their own function manages a transaction. A states
outright that it did not open the file (`01:391`).

### X-2 [SERIOUS] — `cursor.rowcount` is 0 on a `RETURNING` statement until the cursor is exhausted

Executed:

```
UPDATE match   + RETURNING       rowcount_before_fetch=  0  rows=[('b',)]  rowcount_after=1
UPDATE nomatch + RETURNING       rowcount_before_fetch=  0  rows=[]        rowcount_after=0
UPDATE match   no RETURNING      rowcount_before_fetch=  1  rows=[]        rowcount_after=1
```

The research recommended `RETURNING` over `rowcount` because it is more expressive (`04:66-67`) but
never established that **mixing the two idioms is a trap**. It is: read `rowcount` before fetching a
`RETURNING` cursor and you always get 0, i.e. "nothing happened", regardless of what happened.

The repo already knows the correct idiom, and no architect cited it. `RETURNING` appears **exactly
once** in `src/` — `sweep.py:313`:

```sql
RETURNING (recurrence_count = 1) AS was_new
```

It returns a computed boolean *in the result row* and never consults `rowcount`. That is the pattern
every proposal here should have copied, and it is the in-repo precedent for outcome-from-content that
the research argued for abstractly.

This lands squarely on **B1's `steal`** (`02:339-349`), scored there.

### X-3 [SERIOUS] — "one statement needs no transaction" is false as stated, and X-1 is its consequence

A asserts (`01:270`): *"**Transaction shape: none.** One statement, one table, autocommit under
`isolation_level=''`."* Executed:

```
isolation_level=''  in_transaction before=False
in_transaction immediately after UPDATE..RETURNING = True   <-- NOT autocommit
concurrent writer while the 'ceremony-free' txn is OPEN -> database is locked
```

Under `isolation_level=''` Python opens an implicit deferred transaction on the first DML and holds
it — and the write lock — until `conn.commit()`. There is no autocommit.

**A's own cited source says this.** Research CASE 2 (`04:197-200`): *"after INSERT:
in_transaction=True → python's legacy mode opens a txn only on DML, NOT on SELECT."* The research
established the narrow claim *"a guarded UPDATE needs no explicit `BEGIN`"*; A restated it as the
much stronger *"no transaction / autocommit"*. **CONFIDENCE-MONOTONICITY violation**, and not a
harmless one: it is precisely the belief that there is no transaction that makes A1's `pull_next`
wiring (X-1) leak.

The *safety* conclusion happens to survive — DML takes the write lock immediately, so no read
snapshot is pinned and the CASE-3 `SQLITE_BUSY_SNAPSHOT` trap is genuinely unreachable. A is right
about the outcome and wrong about the mechanism, and the wrong mechanism is load-bearing elsewhere.

### X-4 [SERIOUS — convergent rationalization] — `busy_status=None` sells a scope cut as a design win

All three converge: requirements opt out of status projection via a per-kind declaration. A
`01:190-197` (*"Criterion 5 falls out of the declaration"*); B `02:76-81`; C `03:180-183` (*"a
two-word answer"*).

The mechanism is fine. The framing is not. The user's constraints pair the two clauses deliberately:
*"Claim **projects into the entity's status** … First delivery covers **findings and requirements**,
to **prove the generalization rather than assert it**"* (`00:150-153`). Under all three designs,
requirements prove the generalization of the *ownership* half only; the *projection* half — the part
with the hard constraint, the terminal-safety `CASE`, the Q5 restore policy, and the C1 provenance
collision — ships proven by exactly one kind. That is the n=1 that "prove rather than assert" was
written to prevent.

**B is the only architect who says this out loud** (`02:69-73`: *"I claim the constraint is exposing
a defect in the requirement… The two criteria are in tension"*) and it is to B's credit. A and C
present the same scope reduction as free.

### X-5 [WEAKNESS] — the `utc_now()` defect is real, and only two of the nine fixes actually work

Verified: `types.py:12-14` is `strftime("%Y-%m-%dT%H:%M:%SZ")`, whole seconds. Executed:

```
two back-to-back utc_now(): 2026-08-05T21:53:24Z / 2026-08-05T21:53:24Z  equal=True
-> 'claimed_at == renewed_at' discriminator misreports a retry as 'claimed': True
```

The defect A and B both flagged is confirmed: the research's executed contract (`04:317-327`)
distinguishes `claimed` from `already_mine` by comparing `claimed_at` to `renewed_at`, its probe used
hand-written `'10:00'`/`'10:11'` literals (`04:341`), and against the repo's real clock a same-second
retry is misreported.

Do the fixes work?

- **A — millisecond clock. Partially.** I verified the mechanism A depends on and it holds: two
  `strftime('%Y-%m-%dT%H:%M:%fZ','now')` in one statement return an **identical** value
  (`2026-08-05T21:53:24.492Z` / `…492Z`), so a fresh claim reliably yields `claimed_at ==
  renewed_at`. But it only narrows the ambiguity window from 1000 ms to 1 ms; a same-millisecond
  retry still misreports. A concedes this (`01:117-120`).
- **B — `touch_count`. Complete.** A monotone integer incremented by the upsert (`02:255-259`) is
  clock-independent. This is the only fully correct fix in the council for this defect.
- **C — head-verb derivation. Complete.** C decides the outcome from the head event's `actor`, never
  from a timestamp (`03:741-742`). Also clock-independent.

A sub-finding against A: A claims the ms format *"Sorts lexicographically alongside `utc_now()`
output"* (`01:116`). Executed — false: `'2026-08-05T11:22:33.417Z' < '2026-08-05T11:22:33Z'` is
`True`, because `'.'` (0x2E) < `'Z'` (0x5A). A millisecond stamp **always** sorts before a
second stamp inside the same second. Harmless for ordering across seconds; wrong as stated, and it
touches A's `stale_after_minutes` cutoff comparison at second boundaries.

---

## Adjudication: the C9 reversal

**Question put to me:** is C's refutation sound, is the concession sufficient, and what is the honest
performance statement a spec should carry?

### C's refutation is SOUND. Award it.

The research asserted (`04:439-442`, echoed as brief C9) that *"what does agent-N hold"* is a *"full
window fold that no index helps"* at 752 ms / 500k rows. C re-ran it (`03:18-104`) and produced an
anti-join formulation of the identical question at 2.970 ms, with `EXPLAIN QUERY PLAN` showing the
structural difference (`SEARCH … USING INDEX idx_ee_actor_seq` + a covering-index probe, versus
`SCAN entity_events`). The orchestrator reproduced it. **So did I.**

The research's claim "no index helps" is false. It is true of the *window formulation*, which is
`O(n)` by construction — it computes the head event for every entity and discards all but one
actor's — and it is not a property of the substrate. C is right that the research measured a query
plan. This is a genuine, well-evidenced correction and it is the single best piece of technical work
in the three architect documents.

### C's concession is NOT sufficient. The headline number is a fixture artifact.

C concedes the right *shape* — *"Cost scales with `k`, an actor's lifetime claim count, and `k` only
ever grows"* (`03:93`) — and then bounds it with an assumption C explicitly could not verify:
*"agent ids in this system are ephemeral per-worktree strings, not stable identities"* (`03:95`),
restated at `03:1013-1018` as *"the single assumption my recommendation rests on that I could not
measure."*

I measured it. Holding C's own 500k-event / 50k-entity fixture fixed and varying **only** actor
cardinality:

| actors | k (events by the queried actor) | ANTI-JOIN | WINDOW fold |
|---:|---:|---:|---:|
| 1 | 500,000 | **479.918 ms** | 760.124 ms |
| 2 | 250,000 | 182.791 ms | 715.105 ms |
| 4 | 125,000 | 92.071 ms | 724.824 ms |
| 8 | 62,500 | 52.617 ms | 716.595 ms |
| 25 | 20,000 | 22.635 ms | 696.463 ms |
| 200 | 2,500 | **2.422 ms** | 713.729 ms |
| 1000 | 500 | 0.378 ms | 737.214 ms |

Cost is exactly linear in `k` and flat in log size. **The research's 752 ms and C's 2.97 ms are the
same curve sampled at two points.** Neither is "the" number; each is a property of the fixture's
actor distribution. At one actor the anti-join's advantage over the fold it was meant to refute
collapses to **1.6×**.

And C's bound is contradicted by the only evidence in the repo. The sole documented claim identity
anywhere in this system is a **literal constant**: `assignee="claude"` (`SKILL.md:92`) — the very
line all three proposals plan to rewrite. The sole *live* claim call site
(`worktree-setup.sh:209`) passes **no identity at all**, so whatever default an adopter picks becomes
the identity for every agent on the machine. If the adopted holder is a constant — which is what the
repo's own documentation prescribes today — then `n_actors = 1`, `k` = the entire log, and read
path B is **479.9 ms**, i.e. C's refutation would have bought essentially nothing.

C could not have known the `worktree-setup.sh` fact. C could have known that its own recommended
`SKILL.md` replacement text (`03:1070`, `actor="<agent-id>"`) leaves the identity scheme
unspecified, which is the input its entire performance argument is a function of.

### The honest performance statement a spec should carry

> Reverse-ownership lookup ("what does actor X hold") over an append-only claim log costs
> **O(k · log n)**, where `k` is the number of `claim`/`renew` events actor X has emitted over the
> log's lifetime. It is **independent of log size** and unaffected by further indexing once
> `(entity_id, seq DESC)` and `(actor, seq DESC)` both exist; **both are mandatory** — without them
> the query is quadratic (1563 ms at 40k rows, `03:90`). Measured on a 500k-event / 50k-entity log:
> 0.378 ms at k=500, 2.4 ms at k=2.5k, 52.6 ms at k=62.5k, 479.9 ms at k=500k.
>
> The design is therefore **conditional on a bound on per-actor lifetime claim volume**, supplied by
> either high-cardinality (ephemeral) actor identities or log retention. That bound must be a
> **stated and tested invariant** — e.g. a test that fails if any single actor exceeds N events, or
> an enforced identity format — not an assumption. **A spec quoting a single millisecond figure for
> this query is quoting its fixture, not its design.**
>
> Corollary: the identity scheme is not an adoption detail, it is a **performance precondition** of
> the append-only substrate. It must be designed before the substrate is chosen.

---

## Proposal A1: Declared Claim Columns

**FATAL — criterion 8 is unimplementable as written.** See X-1. A's `claim()` commits
(`01:287`) inside `pull_next`'s `BEGIN IMMEDIATE`; executed, that leaks a partial pull and makes the
subsequent `ROLLBACK` raise. A's convergence plan — the answer to criterion 8 and its only claim to
finally wiring `pull_next` (`01:394`) — cannot be built as described. A discloses that it never
opened `capacity.py` this run (`01:391`), which is exactly where the defect is.

**SERIOUS — the claim guard admits terminal entities; there is no `refused` outcome.** The guard is
`AND (claim_holder IS NULL OR claim_holder = :holder)` (`01:260`). Nothing consults status. A
`fixed` finding with no holder passes the guard; the terminal-safe `CASE` (`01:257`) leaves the
status at `fixed`; `RETURNING` yields `claimed_at == renewed_at` (verified identical within one
statement) → the caller is told **`claimed`** and, per A's own contract table (`01:66`), should
"proceed with the work" on a closed bug. A's outcome vocabulary has exactly four values
(`01:61`) and no `refused`. C's has five and refuses on `is_resolved` (`03:216`), and
`EntityRef.is_resolved` already exists at `entities.py:100-103`. This is not theoretical: the shell
implementation already guards it — *"never reopen a `fixed` card"* (`tools/CLAUDE.md:10`). A's SQL
leaves open the case the shell script closed.

**SERIOUS — "Transaction shape: none" is false.** See X-3. A contradicts the research it cites, and
the false belief is what makes the FATAL above fatal.

**WEAKNESS (my own attack, retracted on verification) — the `depends_on` claim is TRUE.** I went
looking for a defect here because A qualified it *"per the legwork read"* (`01:237`), i.e. A relied
on a second-hand read for the mechanism its entire migration ordering rests on. Verified: `db.py:40-95`
— `register_schema(name, ensure_fn, *, depends_on: tuple[str, ...] = ())`, with `_resolve_order()`
running a real `graphlib.TopologicalSorter` that raises on cycles **and on missing dependencies**.
A's design works exactly as described, including the failure mode A worried about: a kind that
declares a `schema_module` nobody registered raises `ValueError` at connect time rather than
silently mis-ordering. The residual finding is only methodological — A shipped a load-bearing
mechanism on an unverified read and happened to be right.

**WEAKNESS — same-millisecond retry misreports; ms/second sort claim is false.** See X-5.

**WEAKNESS — `entities.py` ALTERs other modules' tables.** A nominates this as the strongest attack
(`01:436-440`) and it is real — `CLAUDE.md`'s "no module should reach into another module's tables"
versus `entities.py` issuing `ALTER TABLE findings`. But it is an architecture-taste veto, not a
defect, and A's counter-argument (entities.py is the chartered cross-table module) is respectable. I
rank it *below* the two findings above, which A did not anticipate.

**STRENGTH — the single-statement claim+projection is a real structural advantage.** A's framing
argument (`01:13-17`) survives: if ownership and status live in one row, there is no cross-table
divergence window to engineer away. Every other proposal in this council pays for that window with
either `BEGIN IMMEDIATE` (and X-1) or a "projection may silently fail" clause (C2). A identified the
one genuine benefit its lever confers and did not oversell it.

**STRENGTH — A's independent verification section is the most useful in the council.** It corrected
the brief's C1 line range, found `utc_now()`'s second resolution (confirmed by execution), found the
`query_requirements` meta-filter asymmetry, established that `_SAFE_IDENT` is a test invariant rather
than a runtime guard, and — uniquely — **traced `resolve_trailers`'s caller** and correctly narrowed
the C1 exposure (`01:38-40`) where C escalated it.

**Effort — stated M, honest L.** ~120 lines in `entities.py` + ~200 tests understates: 5 MCP tools
with CLI mirrors at this repo's ~40-lines-per-tool-pair rate, an ALTER-migration test matrix,
`depends_on` ordering, and a criterion-8 deliverable that as specified does not work.

**Viable?** The substrate is sound. The two named deliverables (criterion 8 wiring, the four-outcome
contract) each have a defect. **Repairable, not shippable as written.**

---

## Proposal A2: Meta Claim Envelope

**FATAL — the lost-update hazard is live shipped code, and A's diagnosis is correct.** Verified
independently: `update_finding` does Python-side read-modify-write of `meta` at `findings.py:265`,
`:271` and `:282`, all from the single row read at `:252`, and writes the whole blob back at `:298`.
Any concurrent `meta_update` overlapping a claim stored in `meta` erases it wholesale, and the claim
statement cannot defend itself because it is not the racing statement. A grades this "close to
disqualifying" (`01:585-597`) and re-scores effort S→L. Correct.

**SERIOUS — the headline dies with the fix.** "Zero DDL, zero migration" is the entire case; making
it safe requires converting three `meta` branches in `findings.py` **and** the matching ones in
`reqs.py` to SQL-side `json_patch`. A says so.

**SERIOUS — the reverse query is second-class for requirements.** Verified: `query_findings` has
`meta_key`/`meta_value` (`findings.py:365-368`); `query_requirements` does not. Criterion 4 forbids
exactly this asymmetry.

**SERIOUS — `meta` is an open, forgeable namespace.** Any caller can write `meta.claim` through the
ordinary `update` tool and forge or destroy ownership. Columns cannot be written that way.

**WEAKNESS — the expression-index path is unverified.** A flags it honestly (`01:539-541`). It is
nonetheless the load-bearing performance claim.

**STRENGTH — A's "latent multi-`meta = ?` bug" is real, and it is worse than A said.** A noted
(`01:604-608`) that passing `notes` and `meta_update` together appends two `meta = ?` assignments
built from the same pre-read row, and the later silently wins. Verified at `findings.py:267`/`:284`.
Independently verified that **`reqs.update_requirement` has the identical defect** — two sequential
`json.loads(row["meta"])` blocks, the second discarding the first's mutation, written back at
`reqs.py:222`. That is a pre-existing bug in two modules, found in passing by an architect who was
looking at something else, and it is worth filing regardless of this council's outcome.

**STRENGTH — the "already shipped reverse query" observation is genuine and verified.**
`query(meta_key="claim.holder", meta_value="agent-7")` really does resolve to
`json_extract(meta, '$.claim.holder') = ?` through existing code at `findings.py:365-368`. Nothing
else in this council can point at a working reverse-ownership query that predates the design.

**Effort — stated "S if you accept the hazards, L if you fix them; honest number L."** Honest.

**Viable? No** — and A says so.

---

## Proposal A3: Milestone Spine Claim

**FATAL — "who holds CB-1234" has no single answer.** Verified: `UNIQUE(milestone_id, item_kind,
item_ref)` at `milestones/_schema.py:72` is scoped **per milestone**, so one finding can legally hold
rows in `stream/triage` and `release/1.1` simultaneously. A's claim `UPDATE … WHERE item_ref =
:entity_id` would claim all of them or arbitrarily one. A found this itself and correctly calls it
disqualifying (`01:691-696`).

**FATAL — criterion 4 inverts.** Kind #3 needs a milestone routing rule, an `item_kind` CHECK
migration (a full table rebuild, per the `findings.py:51-97` precedent), and a capacity policy. "No
new ownership code" becomes "three new subsystems' worth."

**SERIOUS — it relocates the "прибито гвоздями" objection** rather than answering it: ownership
becomes nailed to the milestones domain, or `entities.py` must import a domain module.

**STRENGTH — A killed its own most conceptually attractive proposal on verified schema evidence.**
That is exactly the discipline the council needs and it is rarer than it should be.

**Effort — stated L/XL.** Honest.

**Viable? No.**

---

## Proposal B1: Claim Ledger

**FATAL — criterion 8 wiring hard-fails.** See X-1. `claims.claim()` defaults to `project=True`
(`02:312-313`), which enters `db.immediate_txn` (`02:161-176`), whose first statement inside
`pull_next`'s open transaction raises `OperationalError: cannot start a transaction within a
transaction` (executed). Unlike A1's silent leak this is a loud, immediate, total failure of
`pull_next` for every bug and requirement item — which is every item except `external`. B describes
it as *"one line in an existing transaction"* (`02:794`).

**FATAL as specified — `steal` reads `rowcount` on a `RETURNING` statement.** `02:339-349`:

```sql
UPDATE entity_claims SET holder = ?, ... WHERE entity_id = ? AND holder = ?
RETURNING holder;
```
> "If the claim changed hands between the caller's read and its steal, `rowcount == 0` →
> `held_by_other`, no lost update."

Executed (X-2): `cursor.rowcount` on a `RETURNING` statement is **0 before the cursor is fetched**
and only becomes correct after exhaustion. Implemented literally, `steal` **always** reports
`held_by_other` — *while having actually performed the steal*. That is strictly worse than a no-op:
the write lands and is reported as refused, so the caller backs off from an entity it now owns.
B's `release` (`02:326-329`) has the same statement shape but B describes its outcome as "row
returned → released", which is correct — so the defect is localized to `steal` and is a
one-line fix. It is fatal *as written*.

**SERIOUS — `undetermined` can be masked by its own error handler.** B's `immediate_txn` does
`conn.execute("ROLLBACK")` in the `except` (`02:172-174`). When SQLite has already auto-rolled back
(or the transaction never opened), that raises `cannot rollback - no transaction is active`
(executed, P2) — a *new* `OperationalError` whose message contains neither "locked" nor "busy", so
B's `_undetermined` guard (`02:134-135`) re-raises it. The caller gets a raw exception on precisely
the contended path the fourth outcome was invented for.

**SERIOUS — B's answer to its own nominated attack is only half right.** B asks me to hit
`register_post_update_hook` hardest (`02:930-934`). The exception-swallowing copy (`02:388-391`) is a
defensible, disclosed choice with a stated read-time fallback. The real problem B *didn't* raise: the
seam is new `db.py` infrastructure justified by one consumer, and CLAUDE.md's `db.py` charter is
already carrying documented debt. But see the strength below — B under-sold it in the one dimension
that matters.

**WEAKNESS — no history.** Release is a `DELETE` (`02:326`). B concedes; it is the honest cost of the
smallest design.

**WEAKNESS — second ownership representation until convergence** — and the convergence plan is the
FATAL above.

**STRENGTH — `touch_count` is the only complete fix for the `utc_now()` defect** (X-5). A monotone
counter incremented by the upsert is immune to clock resolution entirely, where A's millisecond clock
merely narrows the window. B derived this from a defect in the research's own executed probe
(`02:205-212`) and it is a genuine correction to upstream work.

**STRENGTH — B is the only architect who noticed that going through `update_finding` would commit.**
`_project_claim` (`02:291-301`) writes raw SQL with an explicit contract: *"Runs inside the caller's
transaction and MUST NOT commit."* That is precisely the hazard `findings.py:299` creates, caught and
handled. It makes X-1's failure in B's own convergence plan the more striking — B saw the hazard in
one place and walked into it in another.

**STRENGTH B failed to claim — the `post_update_hook` seam is the ONLY mechanism in the council that
reaches the live call site.** `worktree-setup.sh:209` runs `codebugs update --status in_progress` →
CLI `_cmd_update` (`findings.py:749`) → `update_finding` → `findings.py:298`. A hook fired there
covers the real traffic, whereas every proposal's headline adoption plan (a `SKILL.md` edit) does
not. B argued for the choke point on generality grounds (`02:370-373`) without knowing it was the
*only* grounds that mattered.

**STRENGTH — zero identifier interpolation, `entities.py` untouched, and the honest note that this
is a design preference not a safety one** (`02:49-52`, having verified that `blockers.py:437` already
interpolates registry identifiers with a `noqa`). Intellectually honest in a place where overclaiming
was easy.

**Effort — stated M, itemized.** The most credible estimate in the council for the *module*
(~200 lines + ~250 tests + 40 in `db.py` + 30 in `findings.py`). **Understated for delivery**: it
prices adoption at three one-line wiring edits plus a `SKILL.md` line, when real adoption is a CLI
parameter, a shell-script change in a second repository, and an agent-identity scheme nobody has
designed. Module M, delivery L.

**Viable? Yes, with three corrections** — the closest thing to a shippable design here. See the
closing section.

---

## Proposal B2: Virtual Column Sidecar

**FATAL — speculative generality with zero demand evidence, by B's own search.** B looked for a
second attribute and found none: *"`milestone_items` grew real columns rather than a sidecar"*
(`02:580-581`). An EAV store built to hold exactly one attribute, against a problem C7 downgrades to
API expressiveness, is the clearest over-engineering in the document.

**SERIOUS — it trades the named concept for a magic string at the moment the user asked for the
concept to be named.** B says this itself (`02:574-578`) and it is the sharpest sentence in the
council: *"I argued ownership deserves its own home. This gives it a rented room with a nameplate
reading `attr='owner'`."*

**SERIOUS — untyped `TEXT` values, no per-attribute CHECK, no migration story.** When `owner` must
become `(holder, session)`, every row is a string to reinterpret in Python.

**WEAKNESS — inherits X-1 and X-2 unchanged.**

**STRENGTH — `clear_on_terminal` as a declared property genuinely is better than B1's special case.**
One rule for all attributes rather than one hand-written release path. If a sidecar were ever built,
this is the right shape.

**STRENGTH — B included it explicitly to show the limit of its own thesis** (`02:925-928`) and then
rejected it. That is the correct use of a strawman and it makes B1's scope defensible rather than
merely small.

**Effort — stated M/L.** Honest, and B correctly notes the doc explaining "attribute vs real column"
will be ignored.

**Viable? No** — by its author's verdict, which I endorse.

---

## Proposal B3: Work Sessions

**FATAL — inherits X-1** (nested `immediate_txn` in `pull_next`), unchanged from B1.

**SERIOUS — implicit session selection is racy.** *"when omitted it reuses the agent's newest live
session… or opens one"* (`02:677-679`). That read-then-act runs on a fresh per-request connection
(verified: `server.py:13-19` opens and closes a connection per MCP call), so two concurrent claims by
one agent can each observe no live session and open two. The consequence is not corruption but it
falsifies the design's headline benefit — *"one crashed agent is one row"* (`02:735`) becomes "one
crashed agent is one-to-N rows", and staleness reporting gets noisy in exactly the scenario it exists
for.

**SERIOUS — the `AUTOINCREMENT` history table has no retention policy**, and per my C9 sweep that is
not merely a disk question: any query keyed on `agent_id` over the full history inherits the same
`O(k)` term C2 does, unless it goes through `idx_claim_agent_live`. B's design does route through the
partial index, so B is safe here — but only because of the partial index, and nothing in B3 states
that as a mandatory invariant the way C2's schema comment does.

**WEAKNESS — two exception shapes** (`IntegrityError` = deterministic loss, `OperationalError` =
retryable). B flags it and specifies tests for both (`02:671-676`). Correctly handled; still two more
places to be wrong.

**WEAKNESS — B's own "most speculative against present evidence" concession (`02:756-759`) is now
wrong in B's favour.** B discounted itself against the brief's "never observed" premise, which
§0.1 refutes. B3 is better supported by the evidence than B believed.

**STRENGTH — the best read model in the council.** `CREATE UNIQUE INDEX … ON entity_claims(entity_id)
WHERE released_at IS NULL` plus `idx_claim_agent_live` gives live-set queries at point-lookup cost
**and** full history, with no `O(k)` growth term and no separate projection to keep coherent. This is
the only design here that gets C's audit value without C's performance precondition — and the
substrate was executed by the research (`C_partial`, `04:388-391`).

**STRENGTH — the best C1 answer.** Closing the claim with `release_reason='terminal_status'` rather
than deleting it records *why* ownership ended, citing the commit. Strictly more information than any
mutable design can carry.

**Effort — stated L (~400 + ~400 tests).** Credible and, unusually, probably accurate.

**Viable? Yes, but not now.** B's own trigger condition is the right one (`02:913-915`): build B3 if
history/audit is wanted, and note that B1's table is a strict column-subset so the upgrade is
additive. B is correct that choosing B1 does not foreclose B3.

---

## Proposal C1: Ledger and Lens

**SERIOUS — the lever is evaded, and C says so.** `claims_current` is a mutable one-row-per-entity
table; the lever forbids storing current ownership as directly-mutable state. C's defense is that it
is declared derived and rebuildable, and then C concedes the attack outright: *"a skeptical reviewer
is entitled to say this is Architect B's design wearing a log as a hat. **I think that criticism is
largely correct**"* (`03:654-656`). I agree with C about C. If the answer is a mutable current-claim
row, B1 gets there with one table instead of two and no coherence invariant.

**SERIOUS — the coherence invariant depends on discipline, in a repo with a documented discipline
failure.** C names the mitigation (funnel all appends through a private `_append()`) and the
counter-evidence in the same breath (`03:669-672`, `blockers.py` reaching into private cross-module
functions — which `CLAUDE.md` confirms as known debt).

**SERIOUS — `rebuild_projection` is a `DELETE FROM claims_current` footgun.** C flags it
(`03:658-659`) and requires `BEGIN IMMEDIATE` "which must be remembered" — a mitigation that is a
comment, not a mechanism.

**WEAKNESS — inherits X-1's ambiguity** (unspecified transaction behaviour of `record()`).

**STRENGTH — `rebuild_projection` measured cheaper than a single window-fold read** (58.5 ms for a
whole 500k log). If a projection is built, C is right that this is the honest way: rebuildable,
testable against the log, droppable.

**Effort — stated L (~450 lines).** Credible.

**Viable? Not as the recommendation** — it is B1 plus a log plus an invariant. C reached that
conclusion first.

---

## Proposal C2: Fold on Read *(C's recommendation)*

**SERIOUS — the performance precondition is unstated and unbounded.** See the C9 adjudication. C's
recommendation rests on `k` being bounded by ephemeral actor ids; C could not verify it; the only
documented identity in the repo is the constant `"claude"`, and at one actor read path B is 479.9 ms
— within 1.6× of the number C set out to refute. This does not make C2 wrong; it makes C2
**conditional on a design decision (the identity scheme) that C2 does not make**. As written, the
spec would ship with "2.970 ms" in it and no invariant protecting that number.

**SERIOUS — "the decisive technical argument" rests on a path that may never run.** C ranks C1 as
reason #4 for its recommendation and calls it *"the decisive technical argument and only the log has
it"* (`03:1004-1007`). But `resolve_trailers` is reachable only from its own CLI handler — A verified
this (`01:38-40`) and I confirmed it independently: grepping `/home/faxik/w/autosorter/tools/` and
`.claude/` for `resolve-trailers` / `resolve_trailers` returns **zero hits**, including in
`worktree-finish.sh`, the script that runs after integration and would be its natural caller.
**CONFIDENCE-MONOTONICITY:** the brief states C1 as an obligation — *"must address this writer by
name"* (`00:81-82`) — and C escalates it to a differentiator, without tracing frequency, while A
traced the caller and correctly narrowed the exposure. C's reason #4 should be demoted from
"decisive" to "handled elegantly, on a manually-invoked path."

**SERIOUS — a same-actor concurrent claim appends a duplicate `claim` event and misreports.** C's
guard (`03:751-757`) is:

```sql
WHERE NOT EXISTS (SELECT 1 FROM entity_events h
    WHERE h.entity_id = :eid AND h.verb IN ('claim','renew')
      AND h.actor <> :actor
      AND h.seq = (SELECT MAX(seq) FROM entity_events WHERE entity_id = :eid))
```

If the head is the caller's **own** claim — appended concurrently by another process of the same
actor between C's untransacted step-1 read (`03:727-736`) and this write — then `h.actor <> :actor`
is false, `NOT EXISTS` is **true**, and the insert proceeds with the verb chosen from the stale read:
a **second `claim` event with no intervening release**, and an outcome reported as `claimed` when it
should be `already_mine`. C's own criterion-1 test asserts
`COUNT(*) FROM entity_events WHERE entity_id=? AND verb='claim'` equals 1 (`03:1028`) — that
assertion would fail here, but C's test uses two *different* actors and never exercises it. This is
the same class of read-then-write gap C correctly attacks elsewhere.

**SERIOUS — `projected: false` silently downgrades a HARD user constraint.** C: *"The claim still
stands; only the convenience projection was skipped"* (`03:601-602`, `03:777-778`). The user's
constraint is not a convenience: *"Claim **projects into the entity's status** so existing
`query(status="in_progress")` callers and reports keep working"* (`00:150-152`). Under C2 a claim can
succeed while the entity remains invisible to every existing `in_progress` consumer, reported only in
a return-dict field the caller may not read. C1 (Ledger and Lens) has the same clause. A1's
single-statement design cannot have this failure at all — which is A's strongest genuine advantage
and C does not engage with it.

**WEAKNESS — two claim code shapes** keyed on `busy_status is None` (`03:716-723`). C flags it. The
consequence C does not draw: findings and requirements then exercise **different concurrency code**,
so criterion 1's race test on one kind does not cover the other, and criterion 4's "adding a kind
needs no new ownership code" is true of the declaration but false of the test matrix.

**WEAKNESS — `history()` is unrequested scope**, claimed as a pro (`03:826`) and conceded as scope
(`03:846`) in the same document.

**STRENGTH — the only outcome vocabulary with `refused`.** Five outcomes including refusal on
`kind.claimable=False` or `EntityRef.is_resolved` (`03:216`). This closes the terminal-entity hole
A1 leaves open, using a method that already exists at `entities.py:100-103`.

**STRENGTH — clock-independent outcome derivation** (X-5). Immune to the `utc_now()` defect by
construction rather than by mitigation.

**STRENGTH — §3 (Q6) is the best analytical work in the council.** C was assigned the question,
inventoried 14 hand-rolled state gates across four modules with line citations, found that
`sweep._validate_transition` (`sweep.py:395-407`) is an **existing, working, orphaned** instance of
the very abstraction under debate, then argued **against** its own most ambitious proposal because
most of the 14 gates are not expressible as a DAG. It even flags that a future lift must use
`git-split2` to preserve blame (`03:897-899`), per the user's standing rule. C's own framing is
correct: *"I am arguing against my own most ambitious proposal, and the council should read that as
a real finding rather than modesty."* Agreed — it is the finding I'd keep if I kept nothing else
from these documents.

**STRENGTH — the C9 re-measurement itself.** Sound, reproduced twice independently, and it corrects
an executed upstream result. Award it fully even though the concession is incomplete.

**Effort — stated M (~300 lines).** **Understated.** Omits the two-shape test matrix, the
cross-transaction provenance test C's own caveat demands (`03:443-449`), the mandatory-index
assertion C mandates (`03:859-860`), and the identity scheme its performance claim depends on.
Honest M/L.

**Viable? Conditionally** — and the condition (an enforced actor-identity cardinality invariant) is
larger than C treats it.

---

## Proposal C3: Declared Passage

**FATAL — it changes the behaviour of a widely-called existing function.** Making `update_finding`
reject transitions breaks `provenance.resolve_trailers`, whose posture is to *skip silently*
(`provenance.py:267-269`), not raise. C identifies this (`03:953-957`). It would also break
`worktree-setup.sh:209` the first time an undeclared edge is exercised — a failure in a second
repository, in a shell script, silenced by `>/dev/null 2>&1`.

**FATAL — the consolidation benefit is ~4 of 14 gates and C says so** (`03:949-952`).
`_eligibility_failure`'s five checks (`capacity.py:110-130`) are capacity arithmetic, a free-text
emptiness test, a cross-domain blocker query, and a referential-existence check. A DAG expresses
none of them; encoding them requires a predicate language.

**SERIOUS — it requires a findings DAG nobody has agreed on**, turning a claim primitive into an
open-ended design argument about edges.

**STRENGTH — C priced it honestly and then refused it.** *"Directly contradicts C7… **I concede
it.**"* (`03:960-962`), and *"Proposal 3 below exists to price the alternative honestly, not because
I recommend it"* (`03:369-371`).

**Effort — stated XL.** Honest.

**Viable? No.**

---

## Comparative Analysis

**On substrate, there is nothing to choose.** C2 in the brief settled it and my probes did not
disturb it: four substrates, 200 trials × 4 OS processes, exactly one winner every time
(`04:370-380`). **No proposal claims a safety edge, and that is to all three architects' credit** —
I looked for the violation the orchestrator asked me to hunt and did not find it. A explicitly says
correctness risk is low "with the fewest moving parts"; B says *"Correctness is settled for all
substrates (C2); I claim no advantage there"* (`02:446-447`); C says *"I am **not** claiming my
substrate is safer"* (`03:234`). All three honoured the constraint.

**The real races the probes would not catch — I found three, and they are not where the council
looked:**

1. **Transaction nesting at the integration seam** (X-1) — not a race between two claimants but
   between a claim and the *transaction it was dropped into*. Invisible to any single-primitive
   probe; only appears when you wire it up.
2. **`RETURNING` + `rowcount`** (X-2) — an API artifact, not a concurrency artifact, that
   *presents* as a permanent false "you lost".
3. **The same-actor self-race in C2** — a genuine multi-statement window (untransacted head read →
   guarded append) that every probe missed because every probe raced *different* actors.

**Where the proposals actually differ:**

| axis | best | worst |
|---|---|---|
| claim+projection atomicity | **A1** (one statement, one row — no window exists) | C2 (`projected:false` is a permitted outcome) |
| clock independence | **B1** (`touch_count`), **C2** (head verb) | A1 (1 ms window) |
| reverse query at scale | **B3** (partial index over live claims) | C2 (O(k), unbounded, unstated precondition) |
| audit / history | **B3**, then C2 | B1 (`DELETE` on release) |
| outcome completeness | **C2** (five outcomes, incl. `refused`) | A1 (four; claims terminal entities) |
| terminal-state safety | **C2** | A1 |
| migration cost | A2 (none), then **B1** (new table only) | A3 (table rebuild for kind #3) |
| size vs C7's bar | **B1** ≈ C2 | C3, B2 |
| reaches the live call site | **B1's `post_update_hook`** (accidentally) | all others: none |

**Effort honesty, scored.** B is the most honest estimator (itemized, with a named "definition of
done for adoption", `02:869-871`). C is honest on C1/C3 and understates C2. A is honest on A2/A3 and
understates A1. **Every one of the nine understates adoption**, because all nine price it as a
`SKILL.md` edit and none of them found §0.3.

---

## Convergence Triage

I was asked to treat convergence as possibly-blind. Four of the seven convergent choices are blind.

**CONVERGENCE ON TRUTH** — my attacks land in *different classes* per proposal:

1. **The fourth outcome (`undetermined`).** All three adopted C4. But the failure modes differ: A's
   is fine; B's can be masked by its own `ROLLBACK` handler; C's adds a bounded-2-retry policy that
   is a genuine additional choice. Independent reasoning to a shared conclusion.
2. **`prev_status` capture + guarded restore for Q5.** Executed upstream (`04:382-384`) and each
   architect applied it with different guards. Real.
3. **`_SAFE_IDENT` is dead code.** All three found it independently and all three are right (verified:
   no reference in `entities.py`; the only consumer is `tests/test_entities.py`). Three independent
   confirmations of a brief error is convergence on truth.

**SHARED BLIND SPOT** — my attack is *interchangeable* across all three:

4. **`SKILL.md:92` is the adoption lever.** Attack §0.3 lands identically on all nine. The smuggled
   assumption: *that the brief's C11 enumerated the call sites*. It did not — the brief explicitly
   marked the control flow untraced (`00:134-136`), and all three read C11's enthusiasm ("an
   unusually strong lever") as completion. **This is the classic shape: the brief's confidence tag
   was downgraded in transit by all three consumers simultaneously.**
5. **Wire into `pull_next`'s existing transaction.** Attack X-1 lands on all three, differing only in
   severity. Smuggled assumption: *that a claim primitive composes into a caller's transaction*.
   None of the three checked, and A discloses it never opened the file.
6. **`busy_status=None` resolves criterion 5.** Attack X-4. Smuggled assumption: *that "prove the
   generalization" is satisfied by generalizing the ownership half*. B is the only one to notice.
7. **The race is unobserved, so weight the design down.** Attack §0.1. Smuggled assumption: *that the
   brief's evidence survey was complete*. All three cited it; it is refuted by a comment in the
   adjacent repo. **This blind spot is the most consequential, because it caused all three architects
   to systematically under-argue their own proposals.**

The diagnostic is clean: where the three architects reasoned from the codebase they converged on
truth; where they reasoned from the brief they converged on the brief's errors. **The brief was the
single point of failure, and three parallel architects did not provide the independence that was
supposed to protect against that.**

---

## What Everyone Missed

1. **The race has fired twice, is documented, and is costed** (§0.1). `worktree-setup.sh:58-66`.
2. **The user has already written the post-mortem and it contradicts the council's premise** (§0.2).
   `tools/CLAUDE.md:10`: *"`status=in_progress` is a WRITE-ONLY field that nothing reads, so it never
   stopped anyone."* In both incidents the claim was recorded correctly. The failure was at the read.
   **No proposal here would have prevented either incident.**
3. **The live claim call site is a shell script in another repo, going through the CLI, with no agent
   identity** (§0.3). `worktree-setup.sh:209`.
4. **A working claim already exists in shell with the semantics the council is designing.**
   `worktree-setup.sh:180-215` reads status via `codebugs get`, claims only on `open`, warns on
   `in_progress`, refuses to reopen `fixed`, and has `--allow-duplicate` (= `steal`) and
   `AUTOSORTER_SETUP_NO_CLAIM=1` (= a test bypass). What it lacks is **atomicity and an outcome
   report** — precisely `expected_status` + `changed`, and nothing more.
5. **`cursor.rowcount` is 0 on a `RETURNING` statement until exhaustion** (X-2). Executed.
6. **`in_transaction` is True after any DML under `isolation_level=''`** (X-3). The research said so
   and one architect contradicted it while citing that research.
7. **The identity scheme is a performance precondition, not an adoption detail** (C9 adjudication).
   For the append-only substrate, actor cardinality *is* the performance model.
8. **CLAUDE.md's own claim about autosorter is wrong in an instructive direction.** The project
   `CLAUDE.md` says autosorter's `worktree-setup.sh`/`worktree-finish.sh` call `pull_next` /
   `mark_branch_only` / `mark_integrated` / `release_item` *by name*. Verified: those scripts call the
   **CLI** under different names (`codebugs update`, `codebugs milestone-mark-branch`,
   `codebugs milestone-mark-integrated`), and `pull_next` / `release_item` have **no CLI subcommand at
   all** (`milestones/__init__.py:601-623`) so they cannot be called that way even in principle. The
   brief's fact 2 said "no such script was found"; the scripts exist, they just don't do what
   CLAUDE.md claims. **The `pull_next` cautionary tale is worse than the brief thought**: it is not
   merely unwired, it is documented as wired.

---

## Is Anything Worth Building?

**Honest answer: not any of the nine as scoped.** One small change is worth building, and it is
already in this council — as a footnote, offered by the architect who recommended against his own
proposals.

**C's §8 "Secondary" (`03:1081-1087`), extended by one word I have to add:**

Give `findings.update_finding` an optional keyword-only `expected_status: str | None = None`; when
supplied, append `AND status = ?` to the `WHERE` at `findings.py:298`; return `changed: bool` from
`cursor.rowcount` (valid there — no `RETURNING`); surface it on the MCP `update` tool **and on the
CLI `_cmd_update` (`findings.py:749`)**. The CLI is the word C did not say and it is the one that
matters, because `worktree-setup.sh:209` is the live caller.

Why this, and not a subsystem:

- It is the precise fix for C7's diagnosis — an API-expressiveness problem — at the one choke point
  every status write in the codebase passes through (verified: `findings.py:298`, four call sites).
- **It reaches the live call site.** The nine proposals do not.
- It collapses `worktree-setup.sh`'s hand-rolled `codebugs get` → parse → `codebugs update` sequence
  (a check-then-act race, in shell, today) into one atomic guarded write with a reported outcome —
  closing the only race for which production evidence exists, at the layer where the claim is
  actually made.
- It forecloses nothing. Every design in this document layers on top of it unchanged.

**What it does not give:** an ownership record (criteria 3, 7), requirements coverage (criterion 5),
or the entity-layer capability the user asked for. So the honest thing to put in front of the user is
a question, not a design:

> The two observed incidents were two agents building one card **while the tracker correctly said
> `in_progress`**. That is a read failure. None of these nine proposals fixes it; the git branch-name
> guard already shipped does. If *that* is the harm, this council should close.
>
> If the goal is instead a durable ownership **record** — "what is agent-7 holding right now", stale
> claims visible without a sweeper, requirements covered too — then the work is justified, but its
> value case is **reporting and coordination**, not mutual exclusion. It should be scoped, argued and
> tested as reporting. Mutual exclusion is settled (C2) and, per the user's own post-mortem, was never
> the binding constraint.

**If a ledger is built anyway, build B1 with four corrections:**

1. Fix `steal` — fetch the `RETURNING` cursor before reading `rowcount`, or drop `RETURNING` and use
   a plain guarded `UPDATE` (X-2).
2. Give `claim()`/`release()` a `manage_txn: bool = True` so the `pull_next` call site can pass
   `False`; do not nest `immediate_txn` (X-1).
3. Adopt C2's fifth outcome, `refused`, gated on `EntityRef.is_resolved` — terminal entities must not
   be claimable (the hole in A1, already solved in shell).
4. Design the **agent identity scheme first**, and make `worktree-setup.sh:209` the first consumer.
   Without an identity there is nothing to put in `holder`, and `SKILL.md`'s current answer is the
   constant `"claude"`.

Keep from C, regardless of outcome: the §3 Q6 verdict (no transition engine now) and its
recommendation to record `sweep._validate_transition` in `CLAUDE.md` as the reference implementation
any future unification should lift — with `git-split2`, per the user's standing rule.

Keep from A, regardless of outcome: the explicit `PRAGMA busy_timeout=5000` in `db.connect()`
(`db.py:497`), landed as its own behaviour-neutral commit. Verified: `db.py` contains exactly one
`PRAGMA` and the 5-second timeout the entire clean-loss contract rests on appears nowhere in the
source.

---

## Scorecard

| Proposal | Fatal | Serious | Weakness | Strengths | Viable? |
|---|---:|---:|---:|---:|---|
| **A1** Declared Claim Columns | 1 | 2 | 3 | 2 | **Repairable** — substrate sound; criterion 8 unbuildable, terminal entities claimable |
| **A2** Meta Claim Envelope | 1 | 3 | 1 | 2 | **No** (author concurs) |
| **A3** Milestone Spine Claim | 2 | 1 | 0 | 1 | **No** (author concurs) |
| **B1** Claim Ledger | 2 | 3 | 2 | 4 | **Yes, with 4 corrections** — closest to shippable |
| **B2** Virtual Column Sidecar | 1 | 2 | 1 | 2 | **No** (author concurs) |
| **B3** Work Sessions | 1 | 2 | 2 | 2 | **Yes, but not now** — best read model; build if audit is wanted |
| **C1** Ledger and Lens | 0 | 3 | 1 | 1 | **No** — B1 with a log attached (author concurs) |
| **C2** Fold on Read | 0 | 4 | 2 | 4 | **Conditionally** — needs an enforced actor-cardinality invariant |
| **C3** Declared Passage | 2 | 1 | 0 | 1 | **No** (author concurs) |

Cross-cutting findings X-1…X-5 and §0.1–0.3 are counted once in each proposal they land on.

**One attack of mine was wrong and is retracted in place**: I scored A1's reliance on
`register_schema(depends_on=…)` as a defect because A admitted not opening it; verification showed
the mechanism exists exactly as A described, `graphlib`-backed, with the safe failure mode
(`db.py:40-95`). Downgraded from SERIOUS to a methodological WEAKNESS. Recorded because a review that
only ever confirms its own first reading is not worth much either.

**Overall verdict.** The engineering in all three documents is good and unusually honest — five of
the nine proposals are rejected by their own authors on verified evidence, which is the correct
outcome of a competitive design exercise. The failure is not in the proposals; it is that **all three
architects reasoned from a brief whose evidence survey was incomplete in the one direction that
matters.** The problem statement said the race was never observed and named the wrong call site.
Both are wrong, and the corrections point in opposite directions: the race is *more* real than the
brief allowed, and every proposed fix is *less* relevant to it than the architects assumed, because
the observed failure was a read and all nine designs improve a write.
