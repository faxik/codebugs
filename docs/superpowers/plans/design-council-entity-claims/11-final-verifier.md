# Final Verifier — Round 3 merge gate

**Target:** `10-architect-r3.md` (1876 lines)
**Date:** 2026-08-06
**Mandate:** verify the 13 mandated fixes landed, that nothing deferred was smuggled back, that every
executed claim is real, and that the shell diffs apply and are safe. Not a fresh design review.

**Discipline:** every line cited below was opened this run. Every runtime claim was re-executed by
me, not read. Raw artifacts in
`/tmp/claude-1000/-home-faxik-w-codebugs/349b1511-db4b-4ac3-a327-b85cb19885ba/scratchpad/`:
`verify_p1.py`, `verify_p2.sh`, `verify_txn.py`, `build_patched.py`, `sim.sh`, `sim_finish.sh`.

---

## 0. A label problem that must be fixed before this becomes a spec

**The labels `S1–S4` and `M1–M9` do not exist in the mandate corpus.** Grepping
`08-adversary-r2.md`, `09-judge-r2.md`, `CHECKPOINT-r2.md` and the Codex R2 attack for
`\bM[1-9]\b` / `\bS[1-9]\b` returns **zero hits in all four files**. What actually exists:

| Source | Scheme |
|---|---|
| `08-adversary-r2.md` | module `S-1 … S-5`, shell `F-1 … F-3`, weaknesses `W-1, W-3 … W-6` |
| `09-judge-r2.md:293-330` | an unlabelled four-row shell-defect table |
| Codex R2 | unlabelled prose findings under `major_risks` / `medium_risks` |

`10-architect-r3.md:247` (retraction row R19) says, verbatim:

> "This round uses the Judge's and the adversaries' own labels (S1–S9, M1–M9) and nothing of my own."

**That sentence is false, and R19 is the row conceding that inventing `FATAL-1 … MEDIUM-8` was a
process defect.** The correction commits the same defect and *attributes* the new numbering to other
agents. It also promises `S1–S9` — nine shell labels — where only four shell defects exist.

The document then **overloads the letters**:

- `:128`, `:130` — "this is defect **S2** reproduced", "defect **S4** reproduced" (shell *defects*)
- `:1393`, `:1518` — "Commit **S1** — the claim gate", "Commit **S2** — unconditional release"
  (shell *commits*)

So "S2" denotes both "grep-no-match kills finish" and "the finish-release commit". `M1–M9` is
likewise incomplete: the text references M1, M2, M3, M4, M5, M7, M9 and **never defines M6 or M8**.
An implementer reading "the M2 fix" has no table to resolve it against.

Mechanical to fix (Named Fix 1), but not cosmetic: the audit trail is the mechanism by which the
council's findings are known to have been addressed, and this document breaks the mapping in the
same section where it apologises for breaking it.

Below I reconstruct the canonical 13 from the mandate sources **by substance** and judge the
substance, not the label. My reconstruction (stated so it can be checked):

| Label | Substance | Origin |
|---|---|---|
| S1 | `${BRANCH_NAME}` undefined in `worktree-finish.sh` under `set -u` | Judge table; Codex FATAL #1; R7 |
| S2 | `grep` no-match under `pipefail` kills finish post-merge | Judge; Codex FATAL #2; adversary F-1; R8 |
| S3 | unguarded `who-holds` followed by `case $?` under `set -e` | Judge; Codex FATAL #3; R9 |
| S4 | `ERR` trap not fired by explicit `exit`, installed after the loop | Judge; Codex FATAL #4; adversary F-2; R10 |
| M1 | `entity_terminal` guard gated on projection → unreachable for requirements and `--no-project` | Opus S-1; Codex FATAL #8; R12 |
| M2 | terminal hook not conditioned on CAS `changed` | Codex SERIOUS #9 + both `fix_audit` PARTIALs; R13 |
| M3 | idempotence compares `holder` only, not the full triple | Codex SERIOUS #13; R14 |
| M4 | `capacity.py` → `claims._claim_core` violates CLAUDE.md private-interface rule | Opus S-4; Codex SERIOUS #15 |
| M5 | MCP audit spawns up to 2N git subprocesses | Codex medium #20 |
| M6 | `pull_next` gains a `KeyError` failure mode on orphaned `item_ref`s | Opus S-2 |
| M7 | "third kind by declaration" assumes undocumented schema invariants | Codex medium #23; R18 |
| M8 | `db.txn` reentrancy assumes every ambient transaction has an owning frame | Opus S-3 |
| M9 | `--items=CB-N` creates no claim | Codex medium #26 |

---

## 1. Probe re-execution

I re-ran all four claimed probe sets from scratch. **Nothing was trusted.**

### P1 — NULL-safe holder-triple upsert (§7.2), SQLite 3.47.1

Schema and `INSERT … ON CONFLICT(entity_id) WHERE released_at IS NULL DO UPDATE …` copied verbatim
from §4 and §7.2. Observed:

| Case | Design claims | I observed | Match |
|---|---|---|---|
| fresh `(br-a, branch, /repo/x)` | `touch=1, was_new=1` | `('CLM-1','br-a','branch','/repo/x',…,1,…,1)` | ✅ |
| same triple again | `touch=2, was_new=0` | `touch_count=2, was_new=0`, same `claim_id` | ✅ |
| same holder, `repo=/repo/y` | `None` | `None` | ✅ |
| same holder+repo, `kind=agent` | `None` | `None` | ✅ |
| same holder, `holder_repo=NULL` | `None` | `None` | ✅ |
| fresh with `holder_repo=NULL`, repeated | `touch=1` then `2` | `1` then `2` — `IS` matched NULL to NULL | ✅ |
| live rows after the sequence | `1` | `1` (total rows also 1 — the refused upserts inserted nothing) | ✅ |

Also re-verified §7.4 and §4.1, which the design asserts but does not tabulate: release returns the
row once, double-release returns `None`, a reclaim by a different holder creates a **new** `claim_id`
while the old row survives with `released_at` set, and `entity_id` live count stays exactly 1
throughout. **P1 is real, including the NULL arm a naive `=` would silently fail.**

### P3 — rowcount vs `RETURNING` (§7.6)

| Design claims | I observed |
|---|---|
| `UPDATE … RETURNING`: `rowcount` is 0 before fetch, 1 after | `0` before, `1` after ✅ |
| Same statement without `RETURNING`: 1 on hit, 0 on miss, immediately | `1` / `0` ✅ |
| A `RETURNING` statement read via `rowcount` reports "nothing happened" **while having performed the write** | Confirmed by direct test: `rowcount` never fetched = `0`, table value = `42`. **The write landed.** ✅ |

The in-repo precedent is real and verbatim: `sweep.py:313` is
`RETURNING (recurrence_count = 1) AS was_new`, `.fetchone()` at `:315`.

### P2 — seven shell constructs under `set -euo pipefail` (bash 5.3.9)

| Construct | Design claims | I observed |
|---|---|---|
| `_rc=0; (exit 3) \|\| _rc=$?; case …` | `REACHED rc=3`, `SURVIVED` | identical, rc=0 ✅ |
| `_h=$(echo hi \| grep -oE zzz \|\| true)` | `SURVIVED empty=[]` | identical ✅ |
| same **without** `\|\| true` | rc=1, script died | rc=1, no output ✅ |
| `_h=""; for x in ${_h}` | `SURVIVED_ZERO_ITER` | identical ✅ (control: **unset** `_h` dies with `unbound variable`) |
| `trap EXIT; trap ERR; exit 1` | EXIT fired, ERR **not** | `EXIT_TRAP_FIRED` only ✅ (control: a *failed command* fires both) |
| `trap EXIT; trap - EXIT; exit 0` | disarm works | nothing fired ✅ |
| `grep -vxF -f <(printf …) \|\| true`, partial and total match | `remaining=[fix-cb-1-b]`, `allmerged=[]` | identical ✅ (control: without `\|\| true` the total-match case dies rc=1) |

**All seven reproduce exactly.** I added one construct the design does not probe: an *empty*
`_merged` list. `grep -vxF -f <(printf '%s\n' "")` keeps all lines under `-x`, so it is benign — and
§15.1 guards it anyway with `if [[ -n "${_merged}" ]]`.

### P4 — `capacity.py:234-235` variable name

Opened directly:

```
234:    item = _get_item_by_ref(conn, item_ref)
235:    agent = item.get("assigned_agent")
```

The variable is `agent`. There is no `agent_id` in `release_item`. **D1's claim is exact, verbatim,
including the two-line quote.** ✅

### Bonus: the §0.3 code-fact ledger (27 sub-claims)

Delegated a line-by-line re-open of every row. **26 of 27 exact**, including the load-bearing ones:
`entities.py` is exactly 113 lines; `_SAFE_IDENT` at `:20` has exactly one grep hit in the file (dead
code, as claimed); `grep -rn 'BEGIN' src/codebugs/` returns 6 hits of which **exactly two are
executable** (`merge.py:242`, `capacity.py:182`) as claimed; `merge.py:239-289` really is the
isolation-level save/restore pattern `db.txn` copies; `db.py:229` really is public `row_to_dict`.

One nit: `types.py:33` is cited for `FINDING_STATUS_ALIASES`; the definition is at `:23` and the
`"active" → "in_progress"` entry the sentence actually relies on is at `:32`. The **substance** of
§10.3's P2 example is correct. Off-by-one on a nit citation.

---

## 2. Shell diff verification

This was the highest-risk artifact, so I did not read the diffs — I **applied them**.

`build_patched.py` splices §15.1 (S0), §15.2 (a)(b)(c)(d) and §15.3 (S2) into copies of the **real**
`/home/faxik/w/autosorter/tools/worktree-setup.sh` (274 lines) and `worktree-finish.sh` (1377 lines),
bottom-up so line numbers stay valid.

### Anchors — every one asserted programmatically before splicing

| Anchor | Design says | Real file | |
|---|---|---|---|
| setup `:8` | `set -euo pipefail` | identical | ✅ |
| setup `:81` | `_claim_ids=""` | identical | ✅ |
| setup `:82` | `for cb in ${CB_IDS}; do` | identical | ✅ |
| setup `:88` / `:89` / `:90` | end of `others=`, blank, `if [[ -n "${others}" ]]; then` | identical | ✅ |
| setup `:107` | opens the claim comment block | `# Registry check + claim. Best-effort, …` | ✅ |
| setup `:111` | `if command -v codebugs …` | identical | ✅ |
| setup `:135` / `:136` | `fi` / `done` | identical | ✅ |
| setup `:139` / `:143` | `mkdir -p` / `git … worktree add -b` | identical | ✅ |
| setup `:195/196/197/208/214/215/216` | `fi`, blank, comment, loop start, `done`, blank, `# Optional:` | identical | ✅ |
| setup `:216-233` | the `--items` block, **after** `:143` | `if [[ -n "${ITEMS}" ]]` at `:220`, `fi` at `:233` | ✅ |
| finish `:11` / `:647` / `:1198` / `:1241` | `set -euo pipefail` / `BRANCH=` / `merge --no-ff` / `flock -u 9` | identical | ✅ |
| finish `:1333/1334/1335/1338` | `fi`, blank, `# 8. Clean up worktree`, `worktree remove` | identical | ✅ |

Grep counts re-run by me: setup = 274 lines, `trap` 0, `flock` 0. Finish = 1377 lines,
`BRANCH_NAME` **0**, `branch -d/-D/--delete` **0**, `trap` **0**. **Every §0.1/§0.2 fact is exact.**
The orchestrator's four anchors (`:107`, `:136`, `:143`, `:216-233`) all confirm.

The `(d)` deletion range is the one Codex warned about in Round 2. I checked it by execution:
deleting `197-215` inclusive leaves `:195 fi` → `:196 blank` → `:216 # Optional:`. Both patched
scripts **pass `bash -n`**. No orphaned `fi`/`done`. The design's claim that Codex's warning "does
not apply to this range" is correct.

### The one-fatal-call rule — simulated, not reasoned about

I built a throwaway git repo, a stub `codebugs` that logs its argv and returns a chosen code, and ran
the patched setup script across the failure matrix.

| # | Scenario | Result | Rule holds? |
|---|---|---|---|
| A | `codebugs` absent from PATH | rc=0, worktree created, **zero** codebugs calls | ✅ |
| B | `claim` rc=0 | rc=0, worktree created, one `claim` call | ✅ |
| C | `claim` rc=3 (`held_by_other`) | **rc=1, NO WORKTREE, NO BRANCH** — aborts before `:143` | ✅ intended fatal |
| D | `claim` rc=4 (`entity_terminal`) | rc=1, NO WORKTREE | ✅ intended fatal |
| E | rc=5 then rc=5 | rc=0, warns "stayed contended", **proceeds unclaimed**, worktree created | ✅ |
| F | rc=5 then rc=0 | rc=0, two `claim` calls, worktree created | ✅ retry re-reads the code |
| G | `claim` rc=1 (hard error) | rc=0, warns, proceeds unclaimed | ✅ non-fatal |
| H | `claim` rc=127 (broken install) | rc=0, warns, proceeds unclaimed | ✅ non-fatal |
| I | two cards, 2nd returns rc=3 | rc=1, **`release CB-1111` fired by the EXIT trap**, NO WORKTREE | ✅ **this is the R10/S4 fix working** |
| J | rc=3 + `--allow-duplicate` | rc=0, proceeds **without** a claim; trap has nothing to give back | ✅ |
| K | `AUTOSORTER_SETUP_NO_CLAIM=1` | rc=0, **zero** codebugs calls (the (d) deletion made the env var honest) | ✅ |
| L | `git worktree add` itself fails | rc=255, trap fired, `release CB-1234` issued | ✅ |
| M | failure injected *after* the disarm | rc=1, `claim` only, **no release** — the stated residual leak, behaving as documented | ✅ |
| N | exit-status preservation through a 0-returning EXIT trap | `exit 1` → rc=1; `exit 3` → rc=3 | ✅ trap does not mask the gate's refusal |
| O | S0 ancestry filter, live git | unmerged sibling → rc=1 refusal; only-merged sibling → rc=0, worktree created | ✅ |

**The one-fatal-call rule holds.** Exactly one new call can abort setup — the claim gate — and it
aborts only at rc 3/4, always **before** `mkdir` at `:139` and `git worktree add` at `:143`.

### The finish block — every failure mode reaches cleanup

Ten failure modes of the `[7f/9]` block under the real preamble:

| Mode | rc | Reached step 8? |
|---|---|---|
| `codebugs` absent | 0 | ✅ |
| `claims` empty, rc=0 | 0 | ✅ prints "nothing held" |
| two ids, releases succeed | 0 | ✅ |
| two ids, **release fails (rc=3)** | 0 | ✅ warns, continues |
| `claims` rc=1 (DB not found) | 0 | ✅ |
| `claims` rc=1 with stdout noise | 0 | ✅ (garbage id passed to `release`; harmless) |
| `claims` rc=127 | 0 | ✅ |
| `claims` rc=0 printing a blank line | 0 | ✅ |
| `claims` rc=141 (SIGPIPE) | 0 | ✅ |
| `release` rc=124 (timeout) | 0 | ✅ |

**Control:** the Round-2 shape (`_held=$(echo "{}" | grep -oE "CB-[0-9]+")`) dies rc=1 as the
adversary claimed. The R3 shape survives all ten. **Nothing after `merge --no-ff` at `:1198` can
abort the script.**

---

## 3. The retraction table — spot-checked in both directions

19 rows claimed. I checked the load-bearing ones against the real world, looking for both
under-correction and **over**-correction.

| Row | Verdict |
|---|---|
| R1 (self-accusation of citation error was itself the error) | **Accurate, and correctly self-incriminating.** All four re-verified by me: 274 lines (not 275), `:143` (not `:141`), `:208-214` (not `:206-212`), `:120` (not `:117-125`). §0.1 now carries the corrected values. |
| R2 (branch-existence predicate is dead) | **Accurate.** `git branch -d/-D/--delete` → 0 hits, re-grepped. |
| R3 (`--verify` does not fix it) | **Accurate, executed by me.** `git rev-parse --verify refs/heads/main~1` → `8715a12d0c16…`; `git show-ref --verify --quiet refs/heads/main~1` → rc=1; the same on `refs/heads/main` → rc=0. |
| **R4 (the CB-2534 concession)** | **OVERCORRECTS — see below.** |
| R5, R6 | Accurate; the false-positive arm is genuinely cut (no `who-holds` call survives in either script). |
| R7 (`BRANCH_NAME` in finish) | **Accurate.** grep count 0, re-run. §15.3 uses `${BRANCH}` (`:647`). |
| R8, R9, R10, R11 | **Accurate, and all four reproduced by me** under P2 and the simulation matrix. |
| R12 (M1) | Accurate — §7.1 hoists the terminal test out of both conditions. |
| R13, R14 | Accurate; R14 closed by P1. |
| R15 (`release_item` pseudocode cannot run) | **Accurate, verbatim.** `capacity.py:234-235` opened; variable is `agent`. |
| R16, R17, R18 | Accurate. |
| R19 (the numbering concession) | **Accurate as a concession, but its remedy is false** — see §0. |

### R4 is an overcorrection, and it contradicts the document's own §18.2

R4 marks this Round-2 sentence **FALSE**:

> "CB-2534 is already prevented by shipped code that is not mine"

But `10-architect-r3.md:1777-1779` (§18.2 item 1) says, in the same document:

> "CB-2431 (~40 minutes apart) and CB-2534 (two slugs, 2026-08-04) are **sequential**, and the
> shipped git guard at `:86-105` **already refuses the second launch in both**."

Both cannot be true as written. The retracted sentence is *true of the observed CB-2534 incident* —
which §18.2 affirms — and *false of the collision class*, which is what Codex actually attacked.
Codex's own wording is narrow: *"Shipped git guard does not **fully** prevent the collision class."*

And the Judge's ruling, opened this run at `09-judge-r2.md:102-103`, is explicit:

> "The architect's §1.1 sentence is over-stated and §13.0's concession should be **narrowed to the
> sequential case**."

**Narrowed — not retracted as FALSE.** R4 disobeys the ruling in the direction of giving away more
than was taken, then §18.2 quietly takes it back. This is exactly the "retraction that overcorrects
is also a defect" case. Mechanical to fix (Named Fix 2): restate R4's status as *"the general claim
was over-stated — the guard does not close the concurrent subclass (Codex); the narrower claim that
it would have refused both **observed** incidents stands, and §18.2 relies on it."*

---

## 4. Honesty of the value claim

**Verdict: honest, and not oversold anywhere I could find.**

- §1.4 (`:191-199`) states plainly that both incidents are sequential, quotes the script's own
  comment, says "the shipped guard already refuses both of those", and concludes "the ledger prevents
  a real but so-far-unobserved subclass. … The *incidence* is unmeasured. I am not going to dress
  that up." **I verified the evidentiary base directly:** `worktree-setup.sh:61-63` reads
  *"`fix-cb-2534-debug-rescue-scope` and `fix-cb-2534-2417-documents-router-scope` were built in
  parallel on 2026-08-04, and CB-2431 before them for ~40 minutes"* — the design's §1.4 citation and
  its reading of it are both exact. Likewise `:120`'s *"~41 cards sit in_progress"*, cited in §18.1
  item 1.
- §1.1 ends "**That is the whole justification. There is no second one.**"
- §1.3 deletes rather than repairs Round 2's central sentence, and says the ancestry filter is
  "strictly better than the ledger at the job the ledger was sold on."
- §18.2 item 1 restates it; §20.1 makes the ten-minute concurrency probe **gate 1**, before code.
- §20.3 names the largest risk as "that the gate will be reached", not as a strength.

I grepped the whole document for stronger framing (`prevents CB-`, `eliminat`, `guarantee`,
`solves the`, `makes … impossible`, `no longer possible`). Five hits, all benign: three are about
`git rev-parse --verify`'s semantics, one is the R4 retraction row quoting Round 2, one is §11.5
explicitly *denying* a guarantee. The intro, §18.1 and the §1 summary carry no claim stronger than
§1.4 supports.

One asymmetry worth naming but not blocking: the doc header says *"Status: implementable. An
implementer needs this document and nothing else from Rounds 1–2."* That is a strong claim, and §0's
label problem plus the ambiguities in §6 below are the reasons it is not quite true yet.

---

## Fix Audit (13 rows)

| # | Substance | Where in R3 | Verdict | Evidence |
|---|---|---|---|---|
| **S1** | `${BRANCH_NAME}` undefined in finish | §15.3 `:1536-1538` + block uses `${BRANCH}` | **FIXED** | grep `BRANCH_NAME` in finish = 0 (re-run); `:647` defines `BRANCH`; patched finish passes `bash -n`; simulated block runs clean |
| **S2** | `grep` no-match under `pipefail` kills finish | §15.3 `:1546` — `--format ids` + `\|\| true` on the **substitution** | **FIXED** | 10/10 finish failure modes reach cleanup rc=0; Round-2 control shape dies rc=1 |
| **S3** | unguarded `who-holds` + `case $?` | Arm cut entirely (R6); no `who-holds` call in either script | **FIXED (dissolved)** | grep of both diffs: the only new calls are `claim` (rc-captured), `release` (`\|\| true`/if-guarded), `claims` (`\|\| true`) |
| **S4** | `ERR` trap misses `exit`; installed too late | §15.2(a) `:1404-1418` — **EXIT** trap, installed **before** the loop; disarm at §15.2(c) | **FIXED** | P2 reproduces ERR-vs-EXIT; sim scenario **I** shows CB-1111 released when CB-2222 refuses; scenario N shows exit status preserved |
| **M1** | terminal guard gated on projection | §7.1 `:632-653` — terminal test hoisted above `do_project`, gated only on `allow_terminal` | **FIXED** | Text reads `current = ref.status(conn)  # ALWAYS read — never gated on projection`; both kinds have populated `terminal` (`types.py:36`, `:41`, verified); test 15 covers `FR-1` **and** `project=False` |
| **M2** | hook not conditioned on CAS `changed` | §11.2 `:1092-1106` | **FIXED** | Guard is `status is not None and cur.rowcount == 1 and status != old_status`; applied to **both** `findings` and `reqs`; test 18 asserts all three arms. §11.2 also honestly states the guard is defensive in v1 and load-bearing when `expected_status` lands |
| **M3** | idempotence compares `holder` only | §7.2 `:671-673` | **FIXED** | Full triple with NULL-safe `IS`; **re-executed by me (P1)**, all five arms including NULL |
| **M4** | `capacity.py` → `_claim_core` | §3.1 `:299-309`; D1 | **FIXED (dissolved by deferral) — claim verified, not accepted** | I checked rather than accepting: §6's complete API block has no external `_*_core` caller; a full-document sweep for `pull_next`/`capacity`/`release_item` finds only deferral prose and D1. §3.1's forward plan ("a *public* ambient-transaction API … decided in that commit, not smuggled in here") is stated, not built |
| **M5** | MCP audit spawns 2N git subprocesses | §13 `:1264-1267` | **FIXED (dissolved) — claim verified** | §6 `:623` and §13's five-tool list contain no `audit`/`git_branch_verifier`; document-wide sweep confirms **no git subprocess is invoked from Python anywhere in v1** |
| **M6** | `pull_next` `KeyError` on orphaned `item_ref` | D1; §14 `:1288-1289` | **FIXED (dissolved by deferral)** | §14 states `pull_next` is byte-identical to today; test 9 asserts `tests/test_milestones.py:801-846` passes **unmodified** — the deferral is testable, not asserted |
| **M7** | projection assumes undocumented invariants | §10.3 `:1011-1044` | **FIXED** | Four written preconditions P1–P4; P1–P3 enforced by test 5b iterating `ENTITY_KINDS`; P4 explicitly labelled a review obligation rather than dressed as tested |
| **M8** | `db.txn` assumes every ambient txn has an owner | §5.1 `:432-433`, §5.2 `:492-498` | **PARTIAL** | See below |
| **M9** | `--items=CB-N` creates no claim | §15.4 `:1584-1591`; D11 | **NOT FIXED — explicitly and correctly out of scope** | Verified against the real file: `--items` is parsed at `:22` and consumed at `:220-233`, i.e. **after** `:143`, so a claim there would gate nothing. The stated no-leak argument holds: finish releases only *held* claims |

### M8 — the one PARTIAL, with its reachability established by execution

Opus S-3 attacked `db.txn`'s `if conn.in_transaction: yield False` on the grounds that `db.connect()`
never sets `isolation_level`, so a transaction opened *implicitly* by an earlier statement has **no
owning frame** — and the yield-False branch then writes, never commits, and returns `claimed`.

The design answers only the **explicit** case. `:456` reads: *"`in_transaction` is `True` after an
explicit `BEGIN IMMEDIATE`, so nesting is detected and never attempted."* The implicit case is never
named.

I reproduced the attack against the real module (`verify_txn.py`):

```
isolation_level on a real db.connect() conn: ''        <- legacy implicit mode, confirmed
case 1 (clean conn):  {'outcome':'claimed','this_frame_owns_txn':True}   visible elsewhere: 1
case 2 (dirty conn):  {'outcome':'claimed','this_frame_owns_txn':False}  visible elsewhere: 0
                      ^ the caller was told "claimed" and NOTHING was committed
```

`db.connect()` at `db.py:495` is `sqlite3.connect(path)` with no `isolation_level` — Opus's premise
is exactly right, and `db.py:497` sets `journal_mode=WAL` and nothing else, exactly as §0.3 claims.

**But it is unreachable through any v1 surface, and I verified why:** `server.py:13-19`'s `_conn` is
a context manager that opens a **fresh** `db.connect()` per tool call and closes it; the CLI does the
same. Every public-layer caller therefore holds a clean connection. The only ambient case in v1 is
the terminal hook inside `update_finding`, which uses `_release_core` (not the public layer) and is
committed by `findings.py:299` — verified by reading the function.

So the design is **correct in effect and incomplete in statement**. Since §5.2's own table invites
"external callers" into the public layer, and D1 promises a future `manage_txn=False` API, the
reachability argument must be written down or the next commit re-opens it. Named Fix 3 — one
invariant sentence plus one test row. Not a redesign.

---

## Deferral Audit (7 rows)

I swept the full 1876 lines for each deferred item under multiple spellings, and separately read §6
(complete API), §12.1 (CLI verbs), §13 (MCP tools), §16 (tests), §17 (effort + commit order) and §19.

| # | Deferred item | Found as | Smuggled back? |
|---|---|---|---|
| 1 | `merge.py` refactor onto `db.txn` | §5.1 `:466-471` names `merge.py:242` as a **pre-existing exception** `db.txn` does not touch; §18.2 item 7 "**It changes nothing about `merge.py`**"; D9 | **NO.** Test 24 *ratchets the exception in place* as an allowlist rather than removing it — the opposite of smuggling |
| 2 | `release_item` atomicity fix | R15 (moot), D10 | **NO.** No SQL, no code, no commit-order row |
| 3 | `expected_status` / `changed` public CAS | `:1117-1122`, `:1285-1286`, D2 | **NO** for the public parameter — absent from every signature in §6, from §12.1's four verbs, and from §13's five tools; §14 explicitly says the `"changed"` response key does **not** appear in v1. The **internal** `changed` hook guard (§11.2) is a different thing and was ruled IN by the Judge (item 4 of the R3 fix list) |
| 4 | `steal` | `:623` "No `steal`", `:1225`, D3 | **NO.** D3 sketches what it *would* need; no signature, verb or tool exists |
| 5 | history/summary extras | `:623`, `:1225`, §10.4 `:1049-1052`, D5/D7/D8; `holder_kind='process'` cut from the CHECK list at `:373` | **NO.** §12.1 has exactly four verbs, §13 exactly five tools, and §10.4 *pays* the ergonomic cost in the open rather than quietly adding the `get` block. (`--append-note` appears nowhere at all — the in-scope `--note` at `:1189` is the claim-time note, a different flag) |
| 6 | audit/verifier tooling | `:623`, `:1225`, `:1264-1267`, D4 | **NO.** §13 states no MCP request can reach a subprocess; the design makes **no git subprocess call from Python at all** |
| 7 | `pull_next` integration | §3.1 `:299-309`, §14 `:1288-1289`, §18.2 item 5, D1 | **NO — and it is the only one proved absent by a test.** Test 9 asserts `tests/test_milestones.py:801-846` passes unmodified |

**Zero smuggling.** Notably, the deferrals are load-bearing *against* the architect's interest: §18.2
item 5 and §20.3 both name the resulting single-consumer risk as the largest in the delivery, and
§16.1 refuses to count criterion 8's convergence plan as test coverage.

---

## Probe Re-execution (claimed vs observed)

| Probe | Claimed | Observed by me | Verdict |
|---|---|---|---|
| P1 — NULL-safe holder triple, 7 cases | see §1 table | identical in all 7, plus release/reclaim/live-count re-verified | **REAL** |
| P2 — 7 shell constructs under `set -euo pipefail` | see §1 table | identical in all 7, plus 4 controls I added | **REAL** |
| P3 — rowcount vs `RETURNING` | 0 before fetch, 1 after; no-`RETURNING` immediate | identical; **and I confirmed the write lands while `rowcount` reads 0** | **REAL** |
| P4 — `capacity.py:234-235` variable is `agent` | `item = _get_item_by_ref(…)` / `agent = item.get("assigned_agent")` | byte-identical | **REAL** |
| (extra) R3's `git rev-parse --verify refs/heads/main~1` | returns a SHA; `show-ref --verify` rejects | `8715a12d…` / rc=1 | **REAL** |
| (extra) §5.1's "exactly two executable `BEGIN`" | `merge.py:242`, `capacity.py:182` | 6 grep hits, exactly 2 executable | **REAL** |
| (extra) §0.3's 27 code facts | — | 26/27 exact; one off-by-one nit (`types.py:33` → `:23`/`:32`) | **REAL** |

**No probe was found to be fabricated, exaggerated, or mis-transcribed.** This is the strongest part
of the document.

---

## Shell Diff Verification

**Applies cleanly: YES.** All anchors exact against the real 274- and 1377-line files; both patched
scripts pass `bash -n`; the `:197-215` deletion leaves no orphaned `fi`/`done`.

**One-fatal-call rule: HOLDS.** Verified by simulation, not by reading:

- The **only** new call that can abort is the claim gate at §15.2(b), and only on rc 3/4 — always
  before `mkdir` (`:139`) and `git worktree add` (`:143`). Scenarios C, D confirm no worktree and no
  branch are created.
- Every other new call is guarded: `_claim_one` is rc-captured with `|| _rc=$?`; the trap's `release`
  carries `|| true`; the finish `claims` read carries `|| true` on the substitution; the finish
  `release` is `if`-guarded.
- Failure modes: `codebugs` absent (A), rc 0/1/3/4/5/127 (B–H), grep no-match (finish matrix), trap
  firing early (I, L), trap firing after `git worktree add` (M) — **all behave as specified**.
- Exit status is not masked by the EXIT trap (N).
- S0's ancestry filter works against live git in both directions (O).

**Residual risks, all disclosed by the design itself:** the post-disarm leak (§15.4, scenario M
reproduces it exactly as documented), and `--items` coverage (M9/D11, confirmed structurally
impossible without moving the loop above `:143`).

One cosmetic nit: scenarios C/D print *"claimed by another holder (named above)"*, which depends on
`codebugs claim` having printed its incumbent line to stdout. §12.1 promises "one stable line on
stdout" but never specifies its text — see Ambiguity 5.

---

## Remaining Ambiguities

Ranked. 1–3 are the Named Fixes; 4–7 are nits an implementer can resolve without asking.

1. **The `S`/`M` label scheme is undefined, self-contradictory, and falsely attributed.** `S1–S4` /
   `M1–M9` appear nowhere in the mandate corpus; `S1`/`S2` mean two different things inside R3;
   `M6` and `M8` are never defined; R19 claims the labels come from the Judge and adversaries and
   promises `S1–S9`. **Fix:** add one mapping table (13 rows: label → defect → who found it →
   section that fixes it), rename the shell *commits* to `C0/C1/C2` or `SH-A/B/C`, and correct R19's
   final sentence. Mechanical.

2. **R4 overcorrects and contradicts §18.2 item 1.** Fix: restate R4's status per the Judge's own
   wording at `09-judge-r2.md:102-103` — the concession is **narrowed to the sequential case**, not
   false. One cell. Mechanical.

3. **M8's reachability argument is missing.** The `yield False` branch is silently write-and-never-
   commit on a connection whose transaction was opened implicitly — reproducible, and I reproduced
   it. It is unreachable in v1 only because `server.py:13-19` and the CLI hand out fresh connections.
   **Fix:** add one invariant sentence to §5.2 ("every v1 caller of the public layer holds a fresh
   connection; the `yield False` branch is reachable only from the terminal hook, which
   `findings.py:299` commits — a future ambient consumer per D1 must own its commit") and one test
   row asserting it. Mechanical; no design decision is required because no v1 path reaches it.

4. **Test 25 does not cover CLI exit code 5, and the shell depends on it.** §15.2(b)'s retry branches
   on `_rc == 5`; if the CLI never emits 5, the retry is dead code and a contended DB falls silently
   into `*)` → "continuing unclaimed". Test 13 covers `undetermined` at the **API** level; test 25
   covers codes 0/3/4 and `--format ids` rc 0 but not 5. `release`'s `undetermined` is untested in
   either place. **Fix:** two more assertions on the existing test-25 row. Mechanical.

5. **The human stdout line for `claim`/`release`/`who-holds` is promised but not specified.** §12.1
   says "one stable line on stdout"; §15.2(b)'s message says the incumbent is "named above", so the
   shell UX depends on text that does not exist in the document. An implementer will invent it.

6. **`held_by` / `list_claims` response *shape* is unspecified.** §8's key table is explicitly for
   `claim`/`release` only, and §8 says the reads "return rows, not outcomes" — but both are typed
   `dict[str, Any]`. The wrapper (`{"holder":…, "claims":[…]}` vs `{"count":…, "rows":[…]}`) is left
   to the implementer. Low risk; §7.5 gives the per-row decoration fields.

7. **Citation nits.** `types.py:33` should be `:23` (definition) or `:32` (the `"active"` entry the
   sentence relies on). §0.3 and `:1618` describe the fixture as `tmp_project = db.init_project(…)`;
   the real `tests/test_milestones.py:12-15` discards `init_project`'s return and yields
   `str(tmp_path)`. Both are one-token edits.

**Test-plan coverage, checked as instructed:**

- **Outcome vocabulary (§8).** `claim`: `claimed` (1,2,3,25), `already_mine` (2,3,20),
  `held_by_other` (1,20,25), `entity_terminal` (15,25), `undetermined` (13) — **complete at the API
  level**, incomplete at the CLI level (Ambiguity 4). `release`: `released` (21,22), `not_yours`
  (23), `not_claimed` (23) — `undetermined` **uncovered**.
- **Two-connection race.** Test 1 uses the `tests/test_milestones.py:801-846` shape (2 threads, 2
  `db.connect`, `threading.Barrier(2)`) and — correctly — strengthens it: the precedents assert
  uniqueness only, while test 1 additionally asserts *the loser's outcome string and that the
  response names the winner's full triple*. Test 3 borrows the `tests/test_sweep.py:754-799` 10-thread
  shape. **Both precedents are real** (I re-opened `:801-846` and `:754-799`) and both are used.

---

## VERDICT: SPEC-READY WITH NAMED FIXES

The substance is sound and, unusually, it is sound *by execution*. I re-ran every probe the architect
claimed and every one reproduced. I applied the shell diffs to the real files rather than reading
them: they splice cleanly, pass `bash -n`, and the one-fatal-call rule holds across 15 setup
scenarios and 10 finish scenarios, including the two that mattered most — the abort trap releasing a
first card when a second is refused, and nothing being able to abort the finish script after
`merge --no-ff`. All four shell defects and seven of the nine module defects are genuinely FIXED;
M4, M5 and M6 are genuinely dissolved by deferrals I verified rather than accepted; M9 is correctly
and explicitly out of scope. **No deferred item was smuggled back** — the deferrals are enforced by a
test, not by prose. The value claim is stated at its real, reduced strength in §1.4, §18.2 and §20.1,
and no stronger claim appears anywhere else in the document.

The three named fixes are mechanical and enumerable, and none requires a design decision:

1. **Add the 13-row label mapping table; rename the shell commits off `S1`/`S2`; correct R19's final
   sentence.** (§0, Ambiguity 1)
2. **Restate R4 as "narrowed to the sequential case" per `09-judge-r2.md:102-103`,** so it stops
   contradicting §18.2 item 1. (§3)
3. **Add the M8 reachability invariant to §5.2 (one sentence) and one test row asserting it;** add CLI
   exit-code 5 and `release`'s `undetermined` to test 25. (Ambiguities 3 and 4)

Ambiguities 5–7 are advisory. An implementer can build this document without asking a question once
fixes 1–3 land; today, fix 1 alone would cost them a round-trip, because "the M2 fix" and "commit S2"
cannot be resolved from the document.

**One thing I am explicitly not ruling on**, because it is outside a verifier's remit and the design
already routes it correctly: §20.1's gate 1 — the ten-minute concurrency probe — is a genuine open
empirical question, and §20.3's single-consumer risk is a genuine product decision. Both are
correctly escalated to the user rather than resolved by the architect. Neither blocks spec-writing;
gate 1 blocks *code*, and the design says so.
