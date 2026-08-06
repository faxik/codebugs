# Judge's Assessment — Round 2

**Round:** 2
**Date:** 2026-08-06
**Target:** `07-architect-r2.md` (converged design), against `08-adversary-r2.md` (Opus) and
`codex-attack-r2-result.md` (Codex/Sol).

Everything marked **[verified this run]** below is a command I ran or a file I read myself in this
session. Upstream claims are quoted with their original citation copied through. Where an upstream
citation is wrong, I flag it rather than silently correcting it.

---

## Resolved Since Round 1

**All eight self-nominated fixes are real.** Both adversaries verified this independently, and both
did it by execution rather than by reading:

- Opus: *"No regressions against Round 1's fixed items. All eight self-nominated fixes are real."*
  (`08:686`)
- Codex `fix_audit` (`codex-r2:73-87`): six FIXED, two PARTIAL. The two PARTIALs are both the same
  defect — the terminal hook fires without being conditioned on CAS `changed`: *"both writers fire
  it, but neither firing rule is conditioned on CAS `changed`, allowing release after a refused
  update"* (`codex-r2:81`).

Opus flags, correctly, that the `FATAL-1 … MEDIUM-8` numbering is the architect's own invention in
§17 and appears nowhere in Round 1 (`08:36-41`) — *"The defendant wrote the indictment."* That is a
fair process objection, and Opus did the right thing by checking both directions (did the eight get
fixed, and did the eight drop anything). It found one drop: S-5, the requirements-projection
question, which I rule on separately below.

**The substrate is proven, and this is the most important sentence in my assessment.** The partial
unique index on `WHERE released_at IS NULL` was executed independently by both adversaries against
real SQLite, and both got correct behaviour on every path:

- Codex (`codex-r2:63`): *"Executed on SQLite 3.47.1: fresh claim returned a row, same-holder renew
  returned touch count 2, other-holder returned no row; first release returned a row, double-release
  returned none, reclaim created a new claim ID, and live count remained exactly one. No
  release/reclaim race or double-live API path was found."*
- Opus reached the same result on its own probe (`08`, SERIOUS-6, *"RETRACTED and REDESIGNED,
  correctly"*).

That is success criteria 1, 2 and 3 (`00:171-175`) demonstrated on the actual SQL, by two models,
without collusion. The hard part of this design works. Nothing below should be read as doubt about
the ledger mechanism itself.

Also genuinely resolved: the retraction of the false B1→B3 additive-upgrade claim, the `RETURNING`
/`rowcount` audit, the `undetermined` classifier, and the core/wrapper transaction split. The
architect also volunteered a correction against its own interest (§1.1, the CB-2534 credit), which
Opus called *"the most credible thing in the document"* (`08:453-456`). I agree it was the right
instinct — and, as the next section shows, it went one step too far.

---

## The Guard Contradiction, Adjudicated

The architect claims the shipped guard already prevents the CB-2534 collision class:

> *"**CB-2534 is already prevented, by shipped code that is not mine.** … **Any claim that this
> design prevents CB-2534 is claiming credit git already earned**"* (`07:223-230`)

> *"**So the CB-2534 case — two different slugs for one card, same repo — is already closed by
> shipped code.**"* (`07:1312-1315`)

Opus concurred (`08:441-456`). Codex refused:

> *"`Shipped git guard does not fully prevent the collision class` … **SERIOUS factual reversal** —
> the guard is check-then-act. Two simultaneous setups can both scan before either creates its
> branch and both proceed. The atomic ledger adds real collision prevention; its value is not
> limited to merged-but-undeleted branches."* (`codex-r2:37`)

### I traced the control flow myself

**[verified this run — read `/home/faxik/w/autosorter/tools/worktree-setup.sh:1-160`]**

| Line | What happens |
|---|---|
| `:75-79` | `CB_IDS` parsed out of `${BRANCH_NAME}` |
| `:82` | `for cb in ${CB_IDS}` |
| `:86-88` | `others=$(git -C "${REPO_ROOT}" branch --format='%(refname:short)' \| grep -iE "cb-?${num}([^0-9]\|$)" \| grep -vx "${BRANCH_NAME}" \|\| true)` — a **read** of the ref namespace |
| `:90-105` | refuse (`exit 1`) if `others` non-empty, unless `--allow-duplicate` |
| `:111-135` | `codebugs get` → status warning only. Never gates. |
| `:136` | `done` |
| `:139` | `mkdir -p` |
| `:143` | `git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" …` — the **write** that creates the ref |

**[verified this run]** `grep -n 'flock' worktree-setup.sh` → **no match**. (The `flock` referenced at
`worktree-finish.sh:6` serializes the *merge* at finish time, not setup.)

**Codex is right.** The read at `:86` and the write at `:143` are separated by an unserialized
window containing, per card, one `git branch` invocation, one `codebugs get` Python subprocess
(`:112` — realistically 100–500 ms of interpreter startup), and a `mkdir`. Two setups launched
concurrently for the same card with different slugs both observe `others=""` and both proceed. Git
cannot rescue this: the two branch names *differ by construction* — that is the entire premise of
the bug — so `git worktree add -b` has no ref collision to detect. The guard is a textbook
check-then-act TOCTOU.

**So: does the ledger prevent a collision the shipped guard does not? Yes — exactly one, and it is
real.** Two setups whose `:86` scans both precede either `:143`. `§13.2(b)`'s claim gate runs inside
the same loop, before `git worktree add`, and the unique partial index makes the two claims mutually
exclusive at the database. That is genuine prevention, it needs no history, and it is **not** credit
git already earned. The architect's §1.1 sentence is over-stated and §13.0's concession should be
narrowed to the sequential case.

### But the second half of the question decides how much that is worth

Which case actually happened? The script's own comment (`worktree-setup.sh:57-66`, quoted in
`CHECKPOINT-r1:46`) records CB-2431 as duplicated *"for ~40 minutes"* and CB-2534's two slugs as
*"built in parallel on 2026-08-04"*. Forty minutes apart is not a race — the shipped guard refuses
the second launch outright. "Built in parallel" describes parallel *work*, and does not establish
that the two `worktree-setup.sh` invocations overlapped inside the sub-second `:86`→`:143` window.

**Nobody in this council — not the architect, not either adversary, not me — has established that
either observed incident was a race.** So the marginal collision the ledger prevents is real but
belongs to a subclass that has not been observed. This does not sink the design; it means the
motivation is *partially* restored, not restored to its original strength, and the design must stop
advertising CB-2534 in either direction.

### Both adversaries are right, about different mechanisms

Opus's counter — the table is empty at launch — is correct and I verified its target:

> *"§1-L5's central argument is that **`entity_claims` starts with zero rows** and is never
> backfilled (`07:206-211`, `07:1558-1562`). Therefore on day one … `who-holds` returns **4** and the
> guard refuses exactly as it does today."* (`08:487-489`)

But that attack lands on **§13.2(a)** — the arm that *downgrades* `others` to "leftovers" when
`who-holds` returns exit 3. That mechanism is genuinely inert on day one and ramps only as release
history accumulates. It does **not** land on **§13.2(b)**, the atomic claim gate, which requires no
history whatsoever: two concurrent claims race against each other inside a single run.

**The two findings compose rather than conflict.** The ledger's *mutual exclusion* works from day
one. The ledger's *false-positive reduction* works only after months of accumulated releases. The
design has been arguing for the second and quietly relying on the first.

**Ruling: the ledger's justification is the atomic gate, and only the atomic gate. §13.0's
false-positive argument must be deleted, not repaired** — for the reason in the next section.

---

## Does the Trustworthiness Argument Survive?

§1 makes this the deciding question and answers it with L3:

> *"A claim held by `holder_kind='branch'` is live **iff** `git -C <holder_repo> rev-parse --verify
> refs/heads/<holder>` succeeds. … A branch that was merged and deleted by `worktree-finish.sh` is
> *provably* not being worked on. A branch that exists is *provably* still checked out somewhere."*
> (`07:165-178`)

**No. It does not survive.** Two independent breaks, both verified by me.

### Break 1 — the premise is false in this deployment, and this is fatal on its own

**[verified this run]** In `/home/faxik/w/autosorter/tools/worktree-finish.sh`:
- `grep -n 'branch -d\|branch -D\|branch --delete\|push.*--delete'` → **no match anywhere**.
- `:1338` — `git -C "${REPO_ROOT}" worktree remove "${WORKTREE_PATH}"`.

Opus found the same (`08:461-463`): *"`worktree-finish.sh` runs `git worktree remove` at `:1338` and
contains **no** `git branch -d` / `-D` anywhere (grepped this run)."* Codex found it from the other
end (`codex-r2:25`): *"Direct comparison of local branches with `git worktree list` found many
existing branches in no worktree."*

L3's sentence *"A branch that was merged and deleted by `worktree-finish.sh`"* describes a state
**this repository never produces**. Branches are never deleted. Therefore
`git_branch_verifier` returns `live` for every branch-kind claim ever made — including every dead
one — for the entire life of the table. The predicate is not merely weak; **it has no discriminating
power in the only deployment that will use it.** §1 is right that it is "a fact about the world
rather than an inference about elapsed time"; the problem is that the fact it establishes is not the
fact the design needs.

### Break 2 — the implementation does not implement the stated predicate, and neither would the fix

**[verified this run — read `/home/faxik/w/codebugs/src/codebugs/db.py:210-226`]** `git_rev_parse`
executes `["git", "rev-parse", ref]`. There is no `--verify`. Codex is correct (`codex-r2:23`).

My own probe output, run in `/home/faxik/w/codebugs`:

```
refs/heads/main              -> d171140338e6e09baa660e016ae1aa435efb74ab
refs/heads/main~1            -> b7f86e169b07fa5820aeda2ae195644209178e9d
refs/heads/main^0            -> d171140338e6e09baa660e016ae1aa435efb74ab
refs/heads/main^{commit}     -> d171140338e6e09baa660e016ae1aa435efb74ab
refs/heads/no-such-xyz       -> (fail)
```

**A correction to both adversaries, which neither caught.** Codex frames this as an implementation
gap against a correct spec — the implication being that adding `--verify` closes it. It does not:

```
git rev-parse --verify refs/heads/main~1  -> b7f86e169b07fa5820aeda2ae195644209178e9d
git rev-parse --verify refs/heads/main^0  -> d171140338e6e09baa660e016ae1aa435efb74ab
```

`--verify` guarantees a *single* revision, not an *exact ref*. **The predicate as literally written
in §1 is also wrong**, not just its implementation. The correct primitive is
`git show-ref --verify --quiet "refs/heads/${holder}"`. That is a one-line fix — but it fixes Break
2 only, and Break 1 stands regardless.

### What replaces it

Opus's answer, and it is the right one:

> *"Integration is `--no-ff` (verified at `:1198`), not squash, so every integrated branch is a
> strict ancestor of `main` and `git merge-base --is-ancestor "$b" main` / `git branch --merged`
> identifies it exactly. A one-line filter inside the same loop the design is already patching …
> closes the entire false-positive class the adoption argument rests on, using information git
> already has, with no table, no module, no `db.py` seam, no `entities.py` write path, and no
> second-repo exit-code contract."* (`08:469-476`)

**[verified this run]** All three of its premises hold:
- `worktree-finish.sh:1198` — `git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff --no-verify -m "Integrate ${SLUG}"`. No `--squash` anywhere in the file.
- No branch deletion anywhere (above).
- `:1338` removes the worktree only.

**Ruling.** L3 must be re-based from *ref existence* to *ancestry*: a branch-kind claim is dead when
its branch is an ancestor of the integration branch, in flight when it is not, and unverifiable when
the repo is unreachable. That is falsifiable, it discriminates, and it works on day one with an
empty table.

**And this ruling has a consequence the architect will not like.** Once ancestry is the predicate,
the same one-line test inside the guard loop at `:86-88` closes the merged-but-undeleted
false-positive class **without the ledger** — no table, no history, no exit-code contract, effective
immediately. §13.0's central sentence — *"A claim record that is released at merge time is **the one
thing** that can tell a stale branch from work in flight"* (`07:1329-1331`) — is simply false, and
Opus is right that *"the document has no answer to it"* (`08:478-479`).

So the ledger loses the false-positive justification entirely and keeps the atomic-gate
justification alone. That is a smaller but honest basis, and it is exactly the narrow delivery both
adversaries converged on. **Separately and regardless of whether the ledger ships: add
`git branch --merged` filtering to `worktree-setup.sh`'s guard.** It is a few lines, it is strictly
better than the ledger at the job §13.0 claimed for the ledger, and it should be its own commit
against its own codebug.

---

## Scope Ruling (IN / DEFERRED)

The user approved *"Настоящее поле — строим B1"* (`CHECKPOINT-r1:9`), with the consumer recorded as
*"the agent (or the user) asking the tracker 'is this taken?' before starting work"* (`CHECKPOINT-r1:24`).
That is a real queryable ownership field, with a gate at one call site. I hold the line there.

Opus's scope table (`08:634-644`) puts Round 1's approved footprint at **~520 lines** and Round 2's
at **~1050**, *"Roughly 2×, and the footprint went from 2 source files to 8 plus 2 shell scripts in
another repo"* (`08:644`). Codex independently: *"Scope exceeds the approved ownership record"*
(`codex-r2:57`). The architect's own §16 table (`07:1644-1656`) confirms the 1050.

### IN — this is the approved delivery

1. `entity_claims` with soft delete and the unique partial index on `WHERE released_at IS NULL`.
   Proven by two independent executions.
2. `_claim_core` / `_release_core`, `claim()` / `release()`, contention classification, `touch_count`
   as the outcome discriminator.
3. Reads: `who_holds`, `held_by`, `list_claims`. This is success criterion 3 verbatim.
4. Terminal-status hook in `findings.py` **and** `reqs.py`, **conditioned on CAS `changed`** — the
   one thing both adversaries scored PARTIAL (`codex-r2:81`).
5. `db.txn` — not optional; the FATAL-1 fix depends on it.
6. `PRAGMA busy_timeout=5000` in `db.connect()`, as its own behaviour-neutral commit (my own Round-1
   carry-forward, `06:388-390`).
7. **Adoption, both halves**: the claim gate before `git worktree add` in `worktree-setup.sh`, and an
   unconditional release in `worktree-finish.sh`. `CHECKPOINT-r1:41` already ruled adoption
   first-class. Without it this is the 42nd stale row by the design's own §1-L5 argument.
8. CLI: `claim`, `release`, `who-holds`, `claims` — four verbs, with an **ID output mode**
   (`--format=ids`). Not seven.

### DEFERRED — each to its own commit, its own codebug, or a later round

| Item | Why |
|---|---|
| `merge.py` `db.txn` refactor | The design calls it *"mechanical, behaviour-identical"* (`07:1652`). Touches a module claims never uses. |
| `release_item` atomicity fix | The design itself: *"**This is an atomicity bug fix independent of claims**"* (`07:1186`). A real bug. It deserves its own commit, not a ride-along. |
| `expected_status` + `changed` (+ MCP + CLI) | §12.2 spends a page proving it is *orthogonal to ownership*. That is an argument for shipping it **separately**. **Note: I am deferring my own Round-1 recommendation** (`06:179`) — it is right, it is cheap, and it does not need this design. |
| `milestones/capacity.py` integration (`pull_next` / `release_item`) | Deferring this removes four findings at once: Opus S-1, S-2, S-4, and Codex's *"`release_item` pseudocode cannot run … the design passes `holder=agent_id`, but `release_item` has no `agent_id`; its variable is `agent`"* (`codex-r2:21`). It also removes the CLAUDE.md private-function violation (`codex-r2:31`). **Criterion 8 requires "either subsumed or has a stated convergence plan" (`00:180`) — a stated plan satisfies it.** Keep the written plan; defer the code. |
| Git liveness verifier, `audit`, `claims-audit --prune` | Must be redesigned onto ancestry first. Deferring also kills Codex's MCP-subprocess concern (`codex-r2:43`) and its pre-worktree prune hazard (`codex-r2:15` — a concurrent `--prune` releasing a valid in-flight claim) for free. |
| `steal` | The architect's own cut list (`07:1745`). |
| `history` MCP tool, `claim-history` CLI, `codebugs summary` line, `codebugs get` claim block, `--append-note` exposure, `holder_kind='process'` | Architect's own cut list plus Opus's separables (`08:650-663`). |
| `meta` read-modify-write lost-update bug | Correctly identified and correctly scoped out (`07:1289-1293`). **File it as a codebug.** |

**Resulting budget** — my arithmetic from the architect's own §16 table minus the deferred rows, not
an independently costed estimate: `claims.py` ~200, `db.py` ~40, `findings.py`/`reqs.py` ~20, tests
~250, shell ~40 → **~550, of which ~250 is test code.** That lands within noise of the ~520 the user
approved.

---

## Remaining Blockers

### 1. §1's trustworthiness argument is broken (adjudicated above)

Fatal on its own, and it is the section the architect designates as *"The question that actually
decides this design"* (`07:120-127`). Re-basing L3 on ancestry is not an implementation detail; it
changes why the thing ships.

### 2. Four shell defects, all reproduced by me

**[verified this run — probes run in this session]**

| Defect | My probe | Result |
|---|---|---|
| `${BRANCH_NAME}` undefined in `worktree-finish.sh` under `set -u` | `grep -n 'BRANCH_NAME' worktree-finish.sh` → **zero matches**. Only `BRANCH=$(…)` at `:647`. `set -euo pipefail` at `:11`. | `bash -c 'set -euo pipefail; echo "${BRANCH_NAME}"'` → `unbound variable`, rc=1 |
| `grep` no-match under `pipefail` kills finish | `bash -c 'set -euo pipefail; if true; then echo hi \| grep -oE "zzz" \| while read -r x; do :; done; fi; echo SURVIVED'` | rc=1, `SURVIVED` never printed |
| unguarded `who-holds` under `set -e` | `bash -c 'set -euo pipefail; false; case $? in 3) …;; *) echo reached;; esac'` | rc=1, `case` never reached |
| `ERR` trap not fired by explicit `exit` | `bash -c 'set -euo pipefail; trap "echo TRAP_FIRED" ERR; exit 1'` | rc=1, `TRAP_FIRED` never printed |

All four confirmed. Codex's `major_risks` 1–4 (`codex-r2:3-9`) and Opus's F-1/F-2 (`08:289-337`) are
factually correct, independently, by two models and now by me.

**Are they mechanical, or is the adoption approach wrong?** Individually each fix is one or two
lines — `BRANCH_NAME`→`BRANCH`, a `|| true` or `--format=ids`, a `set +e`/`set -e` wrap, and
`trap … EXIT INT TERM` installed before the loop with a success-path disarm. So: **mechanical, and
the approach is not wrong.** The gate belongs before `git worktree add`; that is the correct place
and §13.2(b) already writes it correctly, including the `set +e` wrap.

**But the pattern is not mechanical, and that is the finding.** All four are the same error: an
unguarded `codebugs` call in a script where **every existing `codebugs` call is guarded**.
**[verified this run]** `worktree-finish.sh:1249-1256`, `:1262`, `:1288`, `:1322` are all
`[[ "${SKIP_CHECKS}" != true ]] && [[ -x … ]]` with a `|| echo "⚠ … (non-fatal)"` tail. The architect
saw the convention and deliberately broke it — *"The convention being broken is the feature"*
(`07:1666`) — then broke it four different ways in one diff.

**Ruling — a structural rule for Round 3, not just a fix list.** **Exactly one new call is permitted
to be fatal: the claim gate before `git worktree add`.** That is the entire point of the design and
it earns its exception. Every other new `codebugs` call in both scripts stays best-effort (`|| true`)
in the shipped style. This cuts the blast radius from four fatal-under-`set -e` sites to one
deliberate one, and it makes F-1 — killing `worktree-finish.sh` *after* `merge --no-ff` has already
landed on main — impossible by construction rather than by care.

Also fold in Codex's contention finding (`codex-r2:11`): the outcome-5 retry tests only Boolean
success, so outcomes 3, 4 and 5 all fall into *"stayed busy; continuing UNCLAIMED"* and a loser
proceeds. Same class, same fix pass.

### 3. Scope is 2× the approval (ruled above)

### 4. S-1 / the terminal guard is conditional on projection

Both models, independently. Codex: *"requirements (`busy_status=None`) and `--no-project` claims
never check terminal status"* (`codex-r2:17`); Opus S-1 same. The terminal check is IN scope and must
not be gated on `busy_status is not None`.

### 5. One user ratification (next section)

---

## Governance: the SETTLED requirements question

`CHECKPOINT-r1:33` reads: *"**Requirements projection is per-kind optional — SETTLED.** The Judge
ruled …"*. I am ruling on my own Round-1 reasoning here, and I split the question in two.

**Was the reasoning correct? Yes.** I said (`06:121-132`) that the conjunction — projection into
status **and** first delivery covering findings + requirements — *"appears in the **brief**
(`00:150-153`), not in the user's ask"*, and that it is *"a conjunction the **brief** manufactured."*
**[verified this run — read `00-problem-brief.md`]** `00:146-153` does present it under the label
*"**Hard, from the user:**"*, and the Problem Statement at `00:3-17` contains nothing about
projection — the user's recorded words are about a design being *"прибито гвоздями"* and about a
capability rather than a findings patch. The factual basis of my ruling holds.

**Was the settlement correct? No, and I am ruling against my own Round-1 self.** This was the single
highest-scored finding of Round 1 across both adversaries — Codex made it `major_risks` #1 and rated
it FATAL against **all nine** proposals: *"the architects renegotiated a hard requirement instead of
satisfying it"* (`codex-r1:3`). A judge may *rule* on such a finding. A judge cannot *settle* it,
because what is being overruled is a line the brief explicitly attributes to the user. **Only the
user can confirm they did not say it.**

What then happened is a citation loop, and Opus caught it exactly:

> *"R2 still opts requirements out (`07:856`), and §8.1 presents it as a *feature* … §17's eight-row
> ledger does not mention it … re-labels it 'SETTLED' without a citable settlement. That is exactly
> the move the design elsewhere condemns as 'laundering'."* (`08:269-280`)

My opinion became the orchestrator's label (`CHECKPOINT-r1:33`), the label became the architect's
authority (`07:864-866`, *"per the SETTLED ruling"*), and the item then vanished from §17's defect
ledger entirely. Three steps from "the Judge thinks" to "not a defect", with no user in the loop.

**Ruling: the reasoning stands; the settlement does not, and it needs the user's explicit
ratification before implementation begins.** One sentence closes it either way:

- *"Correct — I never asked for projection into requirements; the brief added that."* → the current
  design is fine as-is on this axis, and it is now genuinely settled.
- *"No, I want requirements to project too."* → `reqs.py:22`'s CHECK constraint must be reopened, and
  the scope grows well beyond what I ruled IN above.

I note for the record that Opus took the same position and framed it the same way: *"Whatever the
answer, it should be a recorded ruling and not a 'SETTLED' label the architect applied to itself"*
(`08:759-761`).

---

## Readiness

### NOT READY — concurring with both adversaries, with one qualifier neither made.

I considered ALMOST and rejected it, because ALMOST requires me to name a *single* blocker and there
is no honest single blocker here. Three independent areas need work — §1's justification, the shell
diff, and scope — plus one user question. Calling that ALMOST would be dressing up a re-argument as
a fix.

**The qualifier:** what is NOT READY is the *design document*. The *substrate* is READY and
independently proven by two models' executions. This is not a design that needs rethinking; it is a
correct mechanism wrapped in a justification that does not hold and a delivery that is twice the
size approved.

**Round 3 is therefore a targeted fix pass, not a full round.** Do not re-run a council. Hand the
architect a bounded list:

1. **Rewrite §1.** Re-base L3 from ref existence to ancestry (`git merge-base --is-ancestor` /
   `git branch --merged`). Delete §13.0's false-positive argument. Restate the justification as the
   atomic gate closing the check-then-act window between `worktree-setup.sh:86` and `:143` — and
   state plainly that neither observed incident has been shown to be a race.
2. **Rewrite §13.2 and §13.3** under the one-fatal-call rule. Fix all four executed defects.
   `BRANCH`, not `BRANCH_NAME`. `--format=ids`, not JSON-grep. Handle contention outcomes 3/4/5
   distinctly.
3. **Cut to the IN list.** Move every DEFERRED item to its own codebug with a one-line rationale.
4. **Fix S-1** — terminal guard must not depend on `busy_status`. Condition both terminal hooks on
   CAS `changed`.
5. **Correct the citations.** Codex's audit is right and the architect's are wrong:
   **[verified this run]** `wc -l worktree-setup.sh` → **274**, not 275. Codex: *"The architect's
   `:117-125`, `:141`, and `:206-212` are wrong … the target is 274 lines"* (`codex-r2:97`), and its
   warning matters — *"mechanically deleting `:206-212` would leave the current `fi`/`done` tail
   behind and can produce broken shell"* (`codex-r2:99`).
6. **Get the user's ratification on requirements projection** before writing code.

Items 1 and 6 gate the rest. Items 2–5 are a day's careful work by one implementer.

---

## My Recommendation

**Ship the narrow ledger. Do not ship the design as written.**

Three things go to the user now, in this order:

**First, one question — and it is the only thing blocking Round 3 from starting:** did you ever ask
for claims to project into *requirements* status, or did the brief add that? The council closed this
on my Round-1 opinion and then treated the closure as authority. It needs your sentence.

**Second, a correction to the record.** The council spent Round 2 believing the shipped git guard
already prevents the CB-2534 collision class. It does not — it is check-then-act, with an
unserialized window between the scan at `worktree-setup.sh:86` and the worktree creation at `:143`,
and I traced it myself. Codex was right and both the architect and the Opus adversary were wrong on
this. **But** the incidents actually on record (~40 minutes apart) are sequential, not concurrent, so
the guard would have caught them. The ledger's atomic gate is real prevention for a subclass nobody
has yet observed. That is a weaker motivation than the original one, and it should be stated that way
rather than quietly restored.

**Third, two small things worth doing regardless of whether the ledger ships:**
- Add `git branch --merged` / `merge-base --is-ancestor` filtering to `worktree-setup.sh`'s guard.
  Integration is `--no-ff` (`:1198`) and branches are never deleted, so every integrated branch is a
  strict ancestor of main and is identifiable exactly. This closes the entire false-positive class
  the design built a table for — with a few lines, no new state, and effect on day one.
- Add `git branch -d "${BRANCH}"` to `worktree-finish.sh` after the successful merge. Branches
  currently accumulate forever and each one blocks all future branches for its card.

Then the narrow B1: soft-delete ledger, atomic claim/release/query, touch-count outcomes, terminal
hooks guarded by `changed`, the setup gate, the finish release. ~550 lines, half of it tests, one
call site actually changed. That is the real queryable ownership field you approved, and it is the
part that two adversarial models independently executed and could not break.

---

## Hesitations

1. **I am ruling against my own Round-1 settlement, and that should lower your confidence in my
   Round-1 output generally, not just on this item.** The reasoning was sound; my error was not
   flagging that a finding both adversaries scored FATAL needed *your* ratification, not mine. I did
   not notice the label being laundered into authority until Opus pointed at it.

2. **The concurrency window is real but I have not measured it.** I established from the source that
   nothing serializes `:86`→`:143` and that a Python subprocess sits inside it. I did not run two
   concurrent setups and observe a double-create. Before this justification is load-bearing, someone
   should — it is a ten-minute test and it converts my inference into a fact. If the window turns out
   to be practically unreachable in your workflow, the ledger's remaining justification thins further
   and you should hear that before implementation, not after.

3. **My scope ruling defers `pull_next` integration, and that is the item I am least comfortable
   deferring.** Success criterion 8 is satisfied by a stated convergence plan, so it is defensible on
   the letter. But milestones is the one place in-repo where a *second* consumer of ownership already
   exists, and deferring it means the first delivery has exactly one consumer in another repository.
   I deferred it because four separate findings live there — including a pseudocode call that cannot
   run (`codex-r2:21`) — and shipping a broken second consumer is worse than shipping one. If you
   would rather have the second consumer than the smaller diff, say so and it moves to IN with a full
   correctness pass attached.

4. **I have not independently verified the ~1050 and ~520 line figures.** Both come from the
   architect's §16 table (`07:1644-1656`) and Opus's arithmetic on it (`08:634-644`). I read the
   table and the subtraction is right; I did not re-cost the components. My ~550 is the same
   arithmetic minus the deferred rows and inherits the same uncertainty.

5. **A hesitation that changed my ruling rather than sitting beside it.** My first pass had this as
   ALMOST, with §1 named as the single blocker and the shell defects filed as mechanical. Writing out
   the shell section, I could not sustain it: four independently-executed FATALs plus a 2× scope
   overrun are not one blocker with garnish, and the ancestry re-basing is a re-argument of the
   design's purpose rather than a fix to it. I downgraded to NOT READY and kept the qualifier — the
   substrate is ready, the document is not — because the qualifier is the part that tells you Round 3
   is a fix pass and not another council.
