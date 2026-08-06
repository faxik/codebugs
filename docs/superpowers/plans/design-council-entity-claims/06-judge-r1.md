# Judge's Recommendation — Round 1

**Citation discipline.** Every quantitative claim below is either (a) a verbatim quote from an
upstream artifact with its citation copied through, marked *[quoted]*, or (b) something I ran or
read myself this session, marked *[mine]* with the command shown. Where an upstream number did not
survive my re-check, it is in **Hesitations**, not silently corrected.

My own reads this run (`sed`/`grep` in `/home/faxik/w/codebugs`), which I rely on below:

```
sed -n '290,302p' src/codebugs/findings.py     # the unguarded UPDATE at :298, commit at :299
sed -n '746,775p' src/codebugs/findings.py     # CLI _cmd_update
sed -n '968,980p' src/codebugs/findings.py     # argparse for `update`: only --status, --notes
sed -n '573,625p' src/codebugs/findings.py     # MCP update tool
sed -n '18,26p'  src/codebugs/reqs.py          # requirements status CHECK
grep -rn "update_finding(" src/                # 4 call sites
grep -rn "append_note\|append-note" src/codebugs/
grep -n "def update_requirement" -A 10 src/codebugs/reqs.py
grep -n "reqs_update\|reqs-update" src/codebugs/reqs.py
```

---

## 0. The finding that reorders everything

Both adversaries independently recommended `expected_status` + `changed`, extended to the CLI,
because it is "the only change reaching the live caller" (Opus `05:899-913`; Codex
`codex-attack-result.md:89`). **Neither of them traced what the live caller would do with it.**
I did. The answer is: nothing.

Delegated verbatim read of `/home/faxik/w/autosorter/tools/worktree-setup.sh` (agent quotes,
spot-checked by me against the orchestrator's independently-verified facts 1/3/4):

```
111	    if command -v codebugs >/dev/null 2>&1 && [[ -z "${AUTOSORTER_SETUP_NO_CLAIM:-}" ]]; then
112	        status=$(codebugs get "${cb}" 2>/dev/null \
113	            | sed -nE 's/^[[:space:]]*"status":[[:space:]]*"([^"]+)".*/\1/p' | head -1 || true)
114	        case "${status}" in
115	            open)
117	                _claim_ids="${_claim_ids} ${cb}"
119	            in_progress)
120	                # A warning, not a refusal: ~41 cards sit in_progress and a
121	                # known share of those are stale, so refusing here would block
122	                # constantly and train people to pass --allow-duplicate by
123	                # reflex. The branch check above is the one with teeth.
124	                echo "⚠ ${cb} is already marked in_progress in codebugs."
```
```
143	git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${BASE_BRANCH}"
```
```
208	for cb in ${_claim_ids}; do
209	    if codebugs update "${cb}" --status in_progress >/dev/null 2>&1; then
210	        echo "  ✓ ${cb} → in_progress (claimed by ${BRANCH_NAME})"
211	    else
212	        echo "  ⚠ ${cb} → could not claim in codebugs; mark it in_progress by hand"
213	    fi
214	done
```

Four facts follow, and each one lands on a claim an adversary made:

1. **The claim at `:209` runs 66 lines AFTER `git worktree add` at `:143`.** The irreversible act
   has already happened. Opus's case that the small change "closes the only race for which
   production evidence exists, at the layer where the claim is actually made" (`05:910-913`) is
   **wrong at the live call site**: an outcome that arrives after the worktree exists cannot gate
   the worktree.
2. **`update` is only ever called on cards the script just read as `open`** (`:115-117` populate
   `_claim_ids`; `:119-128` warn without claiming). So `--expected-status open` would be
   *satisfied* on essentially every real invocation. It closes a real check-then-act window
   (`:112` → `:209`, spanning worktree creation), but the window's only consequence today is which
   `echo` prints.
3. **The script's exit-code branch already exists and already consumes nothing.** `if codebugs
   update …; then … else … fi` selects a message. *[mine]* `_cmd_update` (`findings.py:746-760`)
   prints `Updated: {id} (status={status})` and returns 0 on any successful update; it distinguishes
   nothing. This is the user's sentence, verbatim, as a live defect — the branch is written, the
   signal it wants does not exist.
4. **The user has ALREADY considered gating on `in_progress` and deliberately refused**, with a
   written reason (`:120-123`): the signal's *precision* is too low — "~41 cards sit in_progress and
   a known share of those are stale." Not its atomicity. Not its API expressiveness. **Every one of
   the nine proposals, and both adversaries' fallback, improves a property the user did not
   identify as the binding one.**

And the incident reconciliation, which no upstream artifact states: `tools/CLAUDE.md:10` says both
cards were "already `in_progress`" *[quoted via orchestrator fact 2]*, while `worktree-setup.sh:200-203`
says "In the CB-2431 collision **one side never locked at all**, so the other side's diligent check
read `open` and was correct-but-useless" *[quoted, orchestrator fact 4]*. These reconcile only one
way: the diligent side claimed correctly; the negligent side **neither read nor wrote the tracker**.
A write-side API cannot reach a caller that never calls. Neither can a read-side one.

---

## Ruling on Each Proposal

| Proposal | Verdict | Key Reason |
|---|---|---|
| **A1** Declared Claim Columns | **REJECTED** | Technically the most repairable of A's three (X-1 is a `manage_txn` param; the terminal hole is `EntityRef.is_resolved`, `entities.py:100-103`). It dies on the user's own constraint, not on a bug: `findings` ends up with `claim_holder`. A says it itself — *"If the objection is literally 'no ownership columns on `findings`,' Proposal 1 is dead on arrival"* (`01:439-440`). The objection is literally that. |
| **A2** Meta Claim Envelope | **REJECTED** | Author concurs. Two independent kill shots: Opus's lost-update (*[mine]* verified — `findings.py:265/271/282` are three `json.loads` blocks off one row read at `:252`, all written back at `:298`), and Codex's sharper one — with `meta={"claim":"user-data"}`, `json_set(…,'$.claim.holder',…)` leaves the scalar unchanged, the `WHERE` still passes, and **two agents are both told they won** (`codex:9`). That is the exact failure the council exists to prevent, produced by the mechanism meant to prevent it. |
| **A3** Milestone Spine Claim | **REJECTED** | Author concurs. `UNIQUE(milestone_id, item_kind, item_ref)` is per-milestone (`milestones/_schema.py:72`), so "who holds CB-1234" has N answers. Criterion 4 inverts: kind #3 needs a routing rule, a CHECK rebuild, and a capacity policy. |
| **B1** Claim Ledger | **FLAWED-BUT-FIXABLE — not now** | Best substrate of the nine; both adversaries agree on that. Opus's two "fatals" are one-line fixes (fetch the `RETURNING` cursor before reading `rowcount`; add `manage_txn: bool`). Codex rejects it for a reason I overturn below (the projection requirement was brief-generated, not user-generated). It is rejected **for now** on proportionality — no demonstrated consumer, and the exclusion job is already done in git. If a ledger is ever built, this is the one. |
| **B2** Virtual Column Sidecar | **REJECTED** | Author concurs, in the best sentence in the council: *"I argued ownership deserves its own home. This gives it a rented room with a nameplate reading `attr='owner'`"* (`02:574-578`). EAV for one attribute, and B searched for a second and found none (`02:580-581`). |
| **B3** Work Sessions | **FLAWED-BUT-FIXABLE — not now** | Genuinely the best read model in the council (partial unique index over live claims: point-lookup cost *and* full history, with no `O(k)` term). B's own trigger is the right one (`02:913-915`): build it if someone asks for claim history. Nobody has. |
| **C1** Ledger and Lens | **REJECTED** | Author concurs: *"this is Architect B's design wearing a log as a hat. **I think that criticism is largely correct**"* (`03:654-656`). Codex adds an executed defect: rebuild produces `(11:00,11:00)` from an online `(10:00,11:00)` (`codex:23`), so the "pure projection" that is its only justification under C's lever is not one. |
| **C2** Fold on Read *(C's own pick)* | **REJECTED** | Three independent read-then-write defects in one guarded append: Codex executed that a `steal` is treated as free and an explicit steal returns no row — *"The core SQL has inverse semantics"* (`codex:5`); Codex executed a release/renew resurrection (`codex:7`); Opus found a same-actor self-race that C's own criterion-1 assertion would fail but C's test never exercises (`05:688-704`). Plus the `O(k)` read is conditional on an identity scheme C2 does not design. Recoverable in principle; three defects in the one statement that *is* the design is a statement nobody has gotten right yet. |
| **C3** Declared Passage | **REJECTED** | Author concurs (*"I concede it"*, `03:960-962`). Codex adds the mechanical kill: the verb CHECK cannot be widened by the shown `ALTER`, so *"every `verb='transition'` insert fails"* (`codex:25`). Consolidates ~4 of 14 gates, by C's own count. |

**On effort, versus the architects' estimates.** Every one of the nine understates, and both
adversaries said so for the same reason: all nine price adoption as a `SKILL.md` edit. I can now
price adoption exactly, and it is worse than either adversary thought — real adoption is a **CLI
parameter, an identity flag, and a restructure of a shell script in a second repository to move the
claim before `git worktree add`**. B1's honest number is Opus's "module M, delivery L" (`05:549-553`);
C2's stated M (`03:848-851`) is an M/L at best.

---

## Adjudicating the Two Adversaries

They agree on the verdict (build no subsystem) and on the fallback. They reached it by disjoint
routes, and the disagreements are informative.

**1. Requirements projection — Codex says FATAL to all nine, Opus says "convergent rationalization." Both are half right, and both miss the real fault.**
Codex: *"the architects renegotiated a hard requirement instead of satisfying it"* (`codex:3`).
Opus (X-4): *"The mechanism is fine. The framing is not"* (`05:224-240`).
**Ruling: Opus is right on the mechanism, Codex is right on the process, and the fault is upstream
of both.** The requirement Codex calls unsatisfiable — projection into `in_progress` for both kinds
— appears in the **brief** (`00:150-153`), not in the user's ask. The user's sentence is *"let a
process know whether it changed the status or it was a no-op."* It says nothing about projection.
*[mine]* `reqs.py:22-23` really does exclude `in_progress`, so Codex's conjunction is real — but it
is a conjunction the **brief manufactured**. Codex correctly caught three architects silently
reframing a stated hard constraint as free (B alone said it out loud, `02:69-73`); Codex incorrectly
treats that as a defect in the design space. It is a defect in the brief. **This is also why B1's
rejection needs a different reason than Codex gave it.**

**2. C2's core SQL — Codex executed two defects Opus missed entirely.** `codex:5` and `codex:7` are
executed counterexamples on SQLite 3.47.1 against the recommended proposal's central statement.
Opus, who executed extensively elsewhere, did not probe C2's statement shape and found a different
third defect by reading. **Award Codex.** This is exactly what the heterogeneous pass is for: Opus
attacked the *integration seams* (transactions, `RETURNING`/`rowcount`, clocks); Codex attacked the
*statements*. Different attack surfaces, minimal overlap, both productive.

**3. A2 — Codex's attack is strictly stronger.** Opus found erasure; Codex found **forgery producing
two winners** (`codex:9`). Award Codex.

**4. C9 / performance — Codex did not engage; Opus did the whole sweep.** Opus's finding stands
unopposed and is the most valuable single piece of new analysis in either review: *"the identity
scheme is not an adoption detail, it is a **performance precondition** of the append-only substrate"*
(`05:348-349`), because the only documented claim identity in the repo is a literal constant, and at
one actor the anti-join is 479.9 ms versus the 752 ms it was meant to refute (`05:306-319`). Award
Opus, fully.

**5. Evidence base — Opus read the adjacent repository; Codex did not.** Opus's §0.1–0.3 (the two
incidents, the user's post-mortem, the live call site) is the single most consequential contribution
to this council, and Codex has no equivalent. Codex reached "build nothing" by proportionality alone
— which, note, is the *more robust* route, since it did not depend on evidence it lacked. Award
Opus on evidence, Codex on reasoning-under-uncertainty.

**6. `_SAFE_IDENT`.** Opus: all three architects were right to call it dead. Codex: it is exercised
by `tests/test_entities.py:147`, so *"A's 'test invariant' wording is accurate; B/C's 'dead code'
heading is overstated"* (`codex:67`). **Codex is more precise.** Minor, but it is the one place Opus
endorsed an imprecision.

**7. Where I overrule both.** Opus states the live call site *"passes no agent identity… An identity
scheme is a prerequisite nobody designed"* (`05:87-89`). **False as stated.** `${BRANCH_NAME}` is in
scope and is echoed on the very next line (`worktree-setup.sh:210`). Identity is *available*; it is
merely not *passed*. And the script says why it records nothing (`:206-207`): *"No `--notes`: that
field is a whole-value overwrite and would destroy the card's existing notes."* See Q-IDENTITY —
this converts Opus's "unsolved prerequisite" into a three-line CLI gap.

Opus's line citations for the shell read-block are also wrong (`05:90-93` cites `:180-195`; the
actual read/`case` block is `:107-135`, and `:180-195` is the venv-import verification). The
substance is right; the range is not. Flagged, not fatal.

---

## My Recommendation

**Build one small thing. Build none of the nine. Close the council.**

### Build: `expected_status` + `changed`, on findings *and* requirements, MCP *and* CLI

Both adversaries converged here and I endorse the *what* — but not their *why*, which §0 refutes.
The honest justification is narrower and it is enough:

1. **It is literally the user's sentence.** "Let a process know whether it changed the status or it
   was a no-op." That is `changed: bool`. Nothing in the request asks for ownership, a subsystem, or
   an entity-layer capability.
2. **It covers requirements with zero migration** — the thing Codex called conjunctively
   unsatisfiable across all nine. `expected_status` guards against whatever statuses a kind already
   has; it never needs `in_progress`. *[mine]* `requirements` has a symmetric surface already:
   `update_requirement` (`reqs.py:163`), MCP `reqs_update` (`reqs.py:625`), CLI `reqs-update`
   (`reqs.py:962`). Criterion 5 falls out for free, and honestly, rather than by opt-out.
3. **Regret cost is near zero.** No new table, no new module, no new MCP server mode, no `--mode`
   slug, no entry in `_ensure_modules_loaded()`. Keyword-only and defaulted, so *[mine]* all four
   existing `update_finding` callers (`provenance.py:265`, `milestones/triage.py:115`,
   `findings.py:596`, `findings.py:749`) are byte-identical. Every design in this council layers on
   top of it unchanged.
4. **It makes the next incident diagnosable.** A `changed=false` return is a data point that does
   not exist anywhere in this system today.

**Concrete scope** (real effort, not an architect's estimate — this is my number):

- `findings.update_finding`: keyword-only `expected_status: str | None`; normalize it through
  `resolve_finding_status` (`types.py:87`) so aliases compare correctly; append `AND status = ?` to
  the `UPDATE` at `findings.py:298`; read `cursor.rowcount` (valid — no `RETURNING`); return
  `changed`. Handle the early return at `findings.py:291-292` (`if not updates`). ~20 lines.
- `reqs.update_requirement`: same, via `resolve_requirement_status` (`types.py:92`). ~15 lines.
- MCP `update` and `reqs_update`: parameter passthrough + docstring. *[mine]* precedent exists —
  the `update` tool already adds a computed key to its result (`unblocked_items`,
  `findings.py:605-608`). ~15 lines.
- CLI `_cmd_update` / `_cmd_reqs_update`: `--expected-status`, print `changed`, and **one deliberate
  design decision**: exit non-zero on `changed=false` only under an opt-in `--fail-if-unchanged`,
  because `worktree-setup.sh:209` consumes the exit code and a silent change of its meaning would
  flip a live shell branch in another repo. ~25 lines.
- Tests: two-connection guarded-race on a file-backed DB (precedent: `tests/test_milestones.py:801`,
  `TestPullNextConcurrent`), no-op → `changed=false`, alias normalization, mismatch performs no
  write, requirements symmetry, backward compatibility for the four existing callers. ~180 lines.

**Total: ~75 lines src across two files + ~180 test lines. Half a day to a day.** For comparison,
the smallest subsystem proposal, B1, is *[quoted]* *"`claims.py` ~200 lines… `tests/test_claims.py`
~250"* (`02:434-440`) — and that is its module cost, before delivery.

### Also build (3 lines, and it is the answer to Q-IDENTITY): expose `--append-note` on the CLI

*[mine]* `update_finding` already has `append_note` (`findings.py:241`, implemented `:270-273`,
used in production by `provenance.py:269`). *[mine]* the CLI argparse for `update`
(`findings.py:972-975`) exposes only `--status` and `--notes`. So the only holder-recording field
available to the live caller is the destructive one — which is exactly why the script records no
identity: *"No `--notes`: that field is a whole-value overwrite and would destroy the card's
existing notes"* (`worktree-setup.sh:206-207`).

Exposing `--append-note` lets `worktree-setup.sh` write `claimed by ${BRANCH_NAME}` non-destructively
with **zero new schema**, and *[mine]* `query_findings` already supports `meta_key`/`meta_value`
(`findings.py:365-368`) for reverse lookup. That is criteria 3 and 7, at a cost of three argparse
lines, as an experiment — not as a subsystem. If it proves load-bearing, **then** build B1.

### Do not build

Any of the nine. Not the entity-claim capability, not the ledger, not the event log, not the
transition engine. The reasoning is in the Dilemma below.

### Not a codebugs change, but say it to the user anyway

The change with actual teeth is in `/home/faxik/w/autosorter/tools/worktree-setup.sh`: move the
claim from `:209` to **before** `git worktree add` at `:143`, and gate creation on it. The script's
own comment already states the intent — *"Claiming here makes the registry authoritative by
construction: creating the worktree IS the claim"* (`:202-203`) — but the code places the claim after
creation, so it isn't. **This is the user's call, not mine**, because the same file already records
a deliberate decision not to gate on `in_progress` (`:120-123`), and my change would re-open that
decision with a higher-precision signal.

---

## The Major Dilemma

*This goes to the user verbatim.*

**The bar you set was "лучше либо сделать хорошо, либо никак." My recommendation is a ~75-line
patch. That looks like neither.**

Here is the honest case both ways.

**For building the capability properly (the case against me).** You looked at this problem and said
it "looks like the seed of a process / workflow," and you were reading something real: the council
found **14 hand-rolled state gates across four modules** (C's §3 inventory), including
`sweep._validate_transition` (`sweep.py:395-407`) — an existing, working, orphaned instance of the
exact abstraction under debate. There genuinely is a missing layer here. A patch on
`update_finding` does not build it, and the thing about missing layers is that every individual
patch is always cheaper than the layer, right up until you have fourteen of them. You also asked for
requirements coverage specifically "to prove the generalization rather than assert it" — you were
guarding against exactly the outcome where the mechanism ships as a findings-shaped special case.

**For building almost nothing (my position).** Three things, in increasing order of force:

1. **The problem you described has already been solved by you, at a better layer.** Your own
   post-mortem: *"`status=in_progress` is a WRITE-ONLY field that nothing reads, so it never stopped
   anyone; the branch-name check has teeth because it is pure git and precedes the irreversible
   act"* (`tools/CLAUDE.md:10`). That is correct, and it generalizes: git worktrees are
   **self-cleaning** — the worktree is removed, the claim is gone — which is a property no tracker
   claim can have without a heartbeat or a sweeper. Criterion 7 ("stale ownership visible without a
   background process") is *already satisfied by git*, and your script says so in as many words:
   *"No branch carries it, so this may be a stale claim"* (`:125`).
2. **The binding constraint is not the one all nine proposals fix.** Your script refuses to gate on
   `in_progress` because *"~41 cards sit in_progress and a known share of those are stale"*
   (`:120-123`). That is a **precision** problem. All nine proposals — and both adversaries'
   fallback — improve **atomicity and expressiveness**. Nobody in this council, including me,
   demonstrated that a more atomic claim makes that 41-card pile more trustworthy. It might: a real
   holder and a real `claimed_at` would let you distinguish live from stale. But git already
   distinguishes live from stale, better and for free.
3. **This repo has a cautionary tale and it is exactly this shape.** `pull_next` is a correct atomic
   claim primitive, 400+ lines, that nothing calls — and `CLAUDE.md` **documents it as wired**
   when *[quoted]* it has no CLI subcommand at all (`05:879-887`). Building the ninth-best version
   of a mechanism whose only live consumer has already routed around it is how you get a tenth
   `pull_next`.

**The synthesis, and where "хорошо" actually applies.** "Do it properly" applies to the problem you
have. You have two, and the council conflated them. The **exclusion** problem is solved — properly,
in git, by you. The **reporting** problem ("what is agent-7 holding, is that claim live") is real,
unsolved, and has **no demonstrated consumer** — nobody has asked it and been unable to answer. My
recommendation gives you the reporting experiment for three argparse lines (`--append-note` + a meta
query that already ships) instead of 450. If someone actually asks the question and the notes field
is not enough, B1 is designed, attacked, and repairable, and you build it then, properly, knowing who
the consumer is.

**Where I could be wrong:** if you know of a consumer for the ownership record that this council
never surfaced — a dashboard, a scheduler, a second orchestrator — then criterion 3 has a real
customer, my proportionality argument collapses, and B1-with-Opus's-four-corrections is the answer.
I looked; I found none. But absence of evidence in a 6-hour council is weaker than your own knowledge
of what you are about to build next.

---

## What This Would Have Done For CB-2431 / CB-2534

Traced against the real control flow, not asserted.

**CB-2431 (~40 min, two builds).** Reconstructing from the two primary sources: `worktree-setup.sh:200-203`
says *"one side never locked at all, so the other side's diligent check read `open` and was
correct-but-useless"*; `tools/CLAUDE.md:10` says the card was "already `in_progress`." Both are true
if the **diligent** side claimed and the **negligent** side never touched the tracker.

- Side A (diligent): reads `open` at `:112`, claims at `:209`. With my change:
  `--expected-status open` → `changed=true`. **Identical behaviour. Correct then, correct now.**
- Side B (negligent): never invoked the tracker. **No API change reaches it.** Not
  `expected_status`, not `held_by_other`, not a claim ledger, not an event log. A write-side outcome
  reaches only a caller that writes; a read-side refusal reaches only a caller that reads.
- **Verdict: prevented by nothing in this council. Prevented by the git branch-name guard
  (`worktree-setup.sh:75-104`), which is unavoidable because creating the worktree is unavoidable.**

**CB-2534 (two slugs, parallel, 2026-08-04).** Both sides ran the script; the pre-existing guard
matched an exact worktree *path*, and `fix-cb-2534-debug-rescue-scope` vs
`fix-cb-2534-2417-documents-router-scope` are different paths (`:60-63`).

- Side 1: reads `open`, creates worktree at `:143`, claims at `:209` → `changed=true`. Unchanged.
- Side 2: reads `in_progress` at `:112` → falls into the `in_progress` arm at `:119`, which
  **warns and proceeds by design** (`:120-123`), and **never adds the card to `_claim_ids`**. So
  side 2 **never calls `update` at all**. `--expected-status` is on a code path side 2 does not
  execute.
- **Verdict: `expected_status` + `changed` would have changed nothing.** The one signal that would
  have — "someone holds this, refuse" — was available to side 2 at `:112` and was deliberately
  downgraded to a warning, because the tracker's `in_progress` is too imprecise to gate on.
- **What did fix it:** the CB-2489/CB-2543 branch-name guard (`:75-104`), pure git, before
  `worktree add`, with `--allow-duplicate` as the explicit override.

**Now the uncomfortable part, stated plainly rather than buried.** Both adversaries recommended this
change partly on the grounds that it "reaches the live call site" (Opus `05:909`) and "closes the
only race for which production evidence exists" (`05:910-913`). **That is convergence on a shared
blind spot, and I am overruling it.** They were right that it reaches the live call site and wrong
that reaching it accomplishes anything: at `:209` the worktree already exists, the result selects an
`echo`, and the caller only ever calls when it just read `open`. Two adversaries from different model
families both stopped at "the CLI is the live path" and neither read forward to `:143` or backward to
`:119-128`.

**I still recommend building it** — but on ground 1 alone (it is precisely what you asked for, it
costs a day, it forecloses nothing, and it makes the next occurrence diagnosable), **not** because it
would have prevented either incident. It would not have.

---

## If We Proceed (Round 2 Focus)

If you accept the small change, **there is no Round 2** — it is a half-day task with an obvious
shape, not a design problem. Write the spec, run the two-connection race test, land it.

Round 2 is warranted only if you overrule me on the Dilemma. In that case, do **not** re-run nine
proposals. The scope would be exactly three questions, in this order — and note that the first one
blocks the other two:

1. **Name the consumer of the ownership record.** Who asks "what is agent-7 holding," from where,
   and what do they do with the answer? If the honest answer is "nobody yet," stop here; that is the
   whole finding. If there is one, its query shape decides B1 (mutable row) vs B3 (partial index +
   history) vs C2 (fold), and no substrate argument before that point is decidable.
2. **Design the identity scheme first, because it is a performance precondition, not an adoption
   detail** (Opus's C9 adjudication, `05:348-349`). Start from the fact both adversaries missed:
   `${BRANCH_NAME}` is already in scope at the live call site (`worktree-setup.sh:210`). A branch
   name is ephemeral, high-cardinality, and is *already* what the git guard keys on — it is the
   right identity, and it makes the git layer and the tracker layer agree by construction.
3. **Restructure `worktree-setup.sh` to claim before `git worktree add`**, or accept that any
   tracker mechanism is advisory. This is the whole adoption question and it lives in another
   repository. If it is not going to happen, ship nothing beyond the small change.

Two things to keep regardless of outcome, both from Opus's closing (`05:942-949`), both of which I
endorse and neither of which needs a council:

- **C's §3 verdict** — no transition engine now — and its recommendation to record
  `sweep._validate_transition` (`sweep.py:395-407`) in `CLAUDE.md` as the reference implementation
  any future unification should lift (with `git-split2`, per the standing rule).
- **An explicit `PRAGMA busy_timeout=5000` in `db.connect()`**, as its own behaviour-neutral commit.
  Opus verified the 5-second timeout the entire clean-loss contract rests on is inherited from
  `sqlite3.connect(timeout=5.0)`'s default and appears nowhere in the source (brief C4,
  `00:94-99`). That is a latent trap independent of everything else here.

Also worth filing regardless: the `meta` read-modify-write lost-update in **both**
`findings.update_finding` and `reqs.update_requirement`, found in passing by Architect A
(`01:604-608`) and independently confirmed by Opus (`05:436-443`). *[mine]* confirmed in findings —
`findings.py:265/271/282` all mutate copies of one row read at `:252`. It is a real shipped bug in
two modules and it is unrelated to whether any of this ships.

---

## Hesitations

1. **My strongest argument rests on a shell comment, not on a trace of what actually happened.**
   "~41 cards sit in_progress and a known share of those are stale" (`worktree-setup.sh:120-121`) is
   the load-bearing evidence for my claim that precision, not atomicity, is the binding constraint.
   I did not verify the 41, and I did not query the DB for the actual stale-claim distribution. If
   that number is wrong or has since changed, my precision argument weakens — though not to zero,
   since the *decision* to warn-rather-refuse is in the code either way.

2. **Number discrepancy, flagged rather than corrected.** The C9 anti-join at 500k rows /
   ~200 actors has three reported values: C's **2.970 ms** (`03:18-104`, via `05:286`), Opus's sweep
   **2.422 ms** (`05:313`), and the orchestrator's re-run **2.5 ms** (established fact 7). Same
   order of magnitude, no contradiction, but three runs produced three numbers — which is itself
   Opus's point (*"A spec quoting a single millisecond figure for this query is quoting its fixture,
   not its design"*, `05:346`). I did **not** re-run it; I am quoting through. The window-fold
   figures I could cross-check did survive: the brief's C9 "82 ms at 40k, 752 ms at 500k" matches
   the research verbatim at `04:436` and `04:441`.

3. **I am recommending against nine proposals partly on "no demonstrated consumer," and absence of
   evidence is the weakest form of argument available.** It is also the argument the user is best
   positioned to overturn in one sentence, which is why the Dilemma puts it to them directly rather
   than burying it in a verdict.

4. **`--append-note` may be scope creep and I nearly cut it.** It is three argparse lines and it is
   the only thing in my recommendation that touches ownership at all. I kept it because it is the
   complete answer to Q-IDENTITY at a cost below the noise floor, and because it converts "build a
   ledger to find out if anyone wants a ledger" into an experiment. **But I have downgraded my own
   framing to match**: it is an experiment, not a deliverable, and if the user wants the strictly
   minimal change, cut it and lose nothing structural.

5. **A hesitation that changed my verdict rather than sitting beside it.** My first pass had
   `expected_status` recommended on the adversaries' reasoning — that it fixes the live race. After
   reading `worktree-setup.sh` end-to-end I established that it does not, and I had to choose between
   dropping the recommendation and re-justifying it. I re-justified it on narrower grounds (it is
   literally the user's ask; near-zero regret cost) and **removed the incident-prevention claim
   entirely**, including from the summary. If the user's real goal was preventing a third CB-2534,
   my recommendation does not deliver it and I would rather say so than let the small change inherit
   credit it has not earned.

6. **I did not independently verify the two adversaries' executed probe outputs.** Codex's SQLite
   3.47.1 counterexamples against C2 (`codex:5`, `codex:7`) and Opus's `RETURNING`/`rowcount` result
   (`05:176-180`) are quoted through, not re-run. I did verify by direct read every `file:line` in
   this repository that I rely on, and every one I checked was accurate — which is the main reason I
   am willing to quote their execution results through. Opus's citations into the *autosorter* repo
   were the exception: the substance held, the line ranges did not (§ "Where I overrule both").
