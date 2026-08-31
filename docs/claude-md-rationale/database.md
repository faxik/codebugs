# Rationale — `## Code rules` → `### Database`

Biography for the rules in `CLAUDE.md`'s Database subsection: review rounds, reproduced
incidents, rejected forms and the measurements a decision was made on. **No rule lives here.**
A line in this file that reads as an instruction is a defect, and its place is the rules layer.

### CB-203 / CB-218 / CB-224 / CB-227 — the three-valued `exists` {#cb-203-трёхзначный-exists}

**Justifies the rules** "`exists` IS THREE-VALUED", "THE PROPERTY, STATED AS A PROPERTY", "THE GATE
KEYS ON CAPABILITY", "THE PROPERTY, AT THE WIDTH IT IS ACTUALLY HELD" and "A SECOND KIND OF
QUESTION" — `CLAUDE.md` → `## Code rules` → `### Database`.

**The measurement that started it (CB-203).** With the execute bit off a `.codebugs/` directory
(`chmod 666`), `codebugs where` printed "no database there yet — the next command creates one" at
exit 0 over a populated tracker every other verb refused. That is strictly worse than CB-100 and
CB-182, where a warning merely went missing; here a false statement stands in its place.
`provenance.py` had already ratified this exact swap for CB-85 one file over, while `db.py` kept the
two-valued spelling — this repository's most-repeated shape.

**Three failed attempts to hold the property in prose, which is the argument for the test.**

*First (CB-218).* The clause read *"at all five sites in `db.py`"*, every word true while the
composition was not: the one place that decides WHICH root those five then inspect ran FIRST and was
still two-valued — not a worse message about the right tracker but a silent bind to the wrong one.

*Second (CB-224).* On the day it wrote *"every question … is three-valued"*,
`_linked_worktree_gitdir`'s own `(gitdir / "commondir").is_file()` and two reads inside
`init_project` (`os.path.isdir`/`os.path.exists`) still answered it with two values.

*Third, the counts.* "Three copies" was four at CB-24; "five sites" was six at CB-218.

**How the gate itself was defeated (CB-227).** It compared the TEXT of a call against a list of
texts, matched an `except` clause only against the literal name `OSError`, and let one
`DECLARED_EXCEPTIONS` row keyed `(function, primitive)` license EVERY call of that primitive in that
function — so a decisive `os.path.exists` restored to `init_project` inherited the licence written
for the harmless `created` flag beside it.

**The two predicates measured side by side.** Over TEN bypasses — the six the card's oracle names
plus four from this unit's own sweep — the old one caught **zero** and the re-keyed one catches
**ten**, while both still catch the two controls and neither reports anything on the unmutated file.

**The width, measured separately.** Forty distinct evasions were swept and RUN against the new
predicate: it catches **fifteen**, and the ones it does not are named one by one in the gate's own
docstring — a miss that is announced costs less than a miss that is not.

**A mutant that discriminated nothing.** The first mutant written for the re-keyed predicate used a
different primitive (`os.path.isdir`), which the old key caught — so that draft of the test
discriminated nothing and was rewritten. A mutant that both predicates refuse is not evidence about
either.

**Measurements for the second kind of question (CB-227), on the tree CB-224 landed on.** `chmod 000`
on a linked worktree's `.git` FILE made `codebugs where` print "no `.codebugs/` found … or any
parent" at exit 1 with the unexamined list EMPTY, with the project's real tracker one hop away, and
made `codebugs init` create a tracker INSIDE that worktree at exit 0, silently — git deletes it with
the worktree, findings and all. `chmod 000` on `commondir` made `where` state as fact "and its main
checkout has no tracker either" over a main checkout that HELD the tracker and that the process had
never located.

**Why counts were removed from the gate's docstring rather than corrected.** It claimed "12 `except OSError` blocks" in `db.py` where the AST says **10**, and "the two `except (OSError, ValueError)`
pairs `_path_state` itself carries" — `_path_state` carries two, the FILE carries three, so the
sentence is right on one reading and wrong on the other, which is its own argument. A third correct
count would rot exactly as the first two did.

---

### CB-199 — one classification point {#cb-199-одна-точка-классификации}

**Justifies the rule** "One classification point covers OPENING A CONNECTION — never a write on a
connection already open", `CLAUDE.md` → `## Code rules` → `### Database`.

**How the narrowing arose.** The claim above named `merge.ensure_schema` as one of the three raise
sites specifically because it ran an UNCONDITIONAL write on every `db.connect()`, so it was — by
accident, not by design — the mechanism that made a read-only DATABASE FILE (reached via the walk
route) fail *inside* `_open()`'s classification, on every verb, including a pure read. CB-195 made
that write conditional on the seed row being missing, which is the whole point of that fix: a purely
reading `db.connect()` must never take the write lock merely to attempt a redundant insert.

**Measured directly, both before and after.** On the unfixed tree, `stats` on a `chmod 444` tracker
file used to refuse at exit 1 with the clean message — a read-only tracker could not even be READ;
on the fixed tree the same `stats` call now succeeds, which is a genuine capability gained, not a
defect.

**The other side of "steady state", measured (CB-202).** Every docstring on this fix qualifies its
promise with those words, and nobody had ever measured what happens on the other side of them. While
a seed row is MISSING — the first open of any tracker, and any tracker whose seed rows were removed
— the insert really does run, and a reading `db.connect()` waits out a concurrent writer exactly as
it did before: **734ms against a 700ms foreign hold, against 0.8ms once the rows exist.**

**How the ratchet was defeated, and by what (CB-202).** The version it replaces asked whether a
string LITERAL sitting in the `conn.execute(...)` call led with a DML verb and sat under an `ast.If`,
and an isolated acceptor defeated both halves at once by moving the same insert into the module's
schema CONSTANT — which the existing `for stmt in SCHEMA.split(";"): if stmt: conn.execute(stmt)`
loop then ran, inside an `if` that tests a Python string's truthiness and nothing about the tracker.
All three of its tests stayed green with the defect fully restored.

**A correction this document made to its own account (CB-213).** It once stood here that the literal
clause was vacuous and that the old rule was structurally inapplicable to the population it audited.
That is false. Schema-init functions in this package DO pass a string literal straight into the call
— `merge.ensure_schema`, `milestones/_schema.ensure_schema` and `reqs.ensure_schema` among them —
and two of those literals are the very `INSERT OR IGNORE` seeds CB-195 repaired. Named as examples
and not as a closed set, deliberately: replacing a false universal with an enumeration would be the
same defect one edit later. What actually made the old rule defeatable was its OTHER clause, *not
nested inside any `ast.If`*: its own two premise tests pinned both directions, flagging the pre-fix
unconditional literal `INSERT` and deliberately NOT flagging a read-gated one, so once CB-195 put
each seed under a guard that reads the row first the rule stopped flagging them — which is correct.
And keying on the name `ensure_schema` left the migration helpers those functions call —
`findings._migrate_statuses`, `reqs._migrate_to_lowercase`, `sweep._migrate` and others —
entirely unread. The acceptor's bypass blinded it a SECOND and INDEPENDENT way. Both mechanisms are real; the
sentence that stood here generalized the second onto the first and asserted a property of this tree
that is false.

**Why the counts were removed rather than corrected.** Two numbers stood in that paragraph — how
many migration helpers there are, and how many execute sites they hold — and both were stale by the
time anyone re-ran them, in the paragraph whose own closing sentence says that a number deciding
anything belongs in a test. A third correct count would rot exactly as the first two did.
