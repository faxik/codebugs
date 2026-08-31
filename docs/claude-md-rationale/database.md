# Rationale — `## Code rules` → `### Database` Biography for the rules in `CLAUDE.md`'s Database subsection: review rounds, reproduced
incidents, rejected forms and the measurements a decision was made on. **No rule lives here.**
A line in this file that reads as an instruction is a defect, and its place is the rules layer.

### CB-203 / CB-218 / CB-224 / CB-227 — the three-valued `exists` {#cb-203-трёхзначный-exists} **Justifies the rules** "`exists` IS THREE-VALUED", "THE PROPERTY, STATED AS A PROPERTY", "THE GATE
KEYS ON CAPABILITY", "THE PROPERTY, AT THE WIDTH IT IS ACTUALLY HELD" and "A SECOND KIND OF
QUESTION" — `CLAUDE.md` → `## Code rules` → `### Database`. **The measurement that started it (CB-203).** With the execute bit off a `.codebugs/` directory (`chmod 666`), `codebugs where` printed "no database there yet — the next command creates one" at
exit 0 over a populated tracker every other verb refused. That is strictly worse than CB-100 and
CB-182, where a warning merely went missing; here a false statement stands in its place.
`provenance.py` had already ratified this exact swap for CB-85 one file over, while `db.py` kept the
two-valued spelling — this repository's most-repeated shape.

**Three failed attempts to hold the property in prose, which is the argument for the test.**

*First (CB-218).* The clause read *"at all five sites in `db.py`"*, every word true while the
composition was not: the one place that decides WHICH root those five then inspect ran FIRST and was
still two-valued — not a worse message about the right tracker but a silent bind to the wrong one.

*Second (CB-224).* On the day it wrote *"every question … is three-valued"*,
`_linked_worktree_gitdir`'s own `(gitdir / "commondir").is_file()` and two reads inside `init_project` (`os.path.isdir`/`os.path.exists`) still answered it with two values.

*Third, the counts.* "Three copies" was four at CB-24; "five sites" was six at CB-218.

**How the gate itself was defeated (CB-227).** It compared the TEXT of a call against a list of
texts, matched an `except` clause only against the literal name `OSError`, and let one `DECLARED_EXCEPTIONS` row keyed `(function, primitive)` license EVERY call of that primitive in that function — so a decisive `os.path.exists` restored to `init_project` inherited the licence written for the harmless `created` flag beside it.

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

**Measurements for the second kind of question (CB-227), on the tree CB-224 landed on.** `chmod 000` on a linked worktree's `.git` FILE made `codebugs where` print "no `.codebugs/` found … or any
parent" at exit 1 with the unexamined list EMPTY, with the project's real tracker one hop away, and
made `codebugs init` create a tracker INSIDE that worktree at exit 0, silently — git deletes it with the worktree, findings and all. `chmod 000` on `commondir` made `where` state as fact "and its main
checkout has no tracker either" over a main checkout that HELD the tracker and that the process had
never located.

**Why counts were removed from the gate's docstring rather than corrected.** It claimed "12 `except OSError` blocks" in `db.py` where the AST says **10**, and "the two `except (OSError, ValueError)` pairs `_path_state` itself carries" — `_path_state` carries two, the FILE carries three, so the
sentence is right on one reading and wrong on the other, which is its own argument. A third correct
count would rot exactly as the first two did.

---

### CB-199 — one classification point {#cb-199-одна-точка-классификации}

**Justifies the rule** "One classification point covers OPENING A CONNECTION — never a write on a
connection already open", `CLAUDE.md` → `## Code rules` → `### Database`. **How the narrowing arose.** The claim above named `merge.ensure_schema` as one of the three raise sites specifically because it ran an UNCONDITIONAL write on every `db.connect()`, so it was — by
accident, not by design — the mechanism that made a read-only DATABASE FILE (reached via the walk
route) fail *inside* `_open()`'s classification, on every verb, including a pure read. CB-195 made
that write conditional on the seed row being missing; the point of that fix was that a purely
reading `db.connect()` was taking the write lock merely to attempt a redundant insert. **Measured directly, both before and after.** On the unfixed tree, `stats` on a `chmod 444` tracker
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
schema CONSTANT — which the existing `for stmt in SCHEMA.split(";"): if stmt: conn.execute(stmt)` loop then ran, inside an `if` that tests a Python string's truthiness and nothing about the tracker.
All three of its tests stayed green with the defect fully restored.

**A correction this document made to its own account (CB-213).** It once stood here that the literal
clause was vacuous and that the old rule was structurally inapplicable to the population it audited.
That is false. Schema-init functions in this package DO pass a string literal straight into the call
— `merge.ensure_schema`, `milestones/_schema.ensure_schema` and `reqs.ensure_schema` among them — and two of those literals are the very `INSERT OR IGNORE` seeds CB-195 repaired. Named as examples
and not as a closed set, deliberately: replacing a false universal with an enumeration would be the
same defect one edit later. What actually made the old rule defeatable was its OTHER clause, *not
nested inside any `ast.If`*: its own two premise tests pinned both directions, flagging the pre-fix unconditional literal `INSERT` and deliberately NOT flagging a read-gated one, so once CB-195 put
each seed under a guard that reads the row first the rule stopped flagging them — which is correct.
And keying on the name `ensure_schema` left the migration helpers those functions call — `findings._migrate_statuses`, `reqs._migrate_to_lowercase`, `sweep._migrate` and others —
entirely unread. The acceptor's bypass blinded it a SECOND and INDEPENDENT way. Both mechanisms are real; the
sentence that stood here generalized the second onto the first and asserted a property of this tree
that is false.

**Why the counts were removed rather than corrected.** Two numbers stood in that paragraph — how
many migration helpers there are, and how many execute sites they hold — and both were stale by the
time anyone re-ran them, in the paragraph whose own closing sentence says that a number deciding
anything belongs in a test. A third correct count would rot exactly as the first two did.

---

### CB-43 — the findings identity function {#cb-43-функция-тождества-находок}

**Justifies the rule** "Findings have an identity function (CB-43): `add` is an upsert, not an insert", items (1)–(13), `CLAUDE.md` → `## Code rules` → `### Database`.

**Item (3), the measurement behind "vary your default description".** 158 of 173 test call sites
create fixtures from identical tuples, which is why a fixture helper that wants N distinct entities
gets one row unless it varies something the fingerprint reads.

**Item (4), why the description is stripped of its own meta values.** Measured collapse on the
motivating corpus family was **0/115** without meta-stripping and **71/115** with it.

**Item (6), the deferral that was later honoured.** This clause once deferred re-keying to a
"future card"; CB-61 negotiated exactly one such operation, and the relaxation was declared in the
ONE function that received it rather than generalized.

**Item (7), what the old import guard got wrong, in both directions.** The rule used to read "CSV
import skips rows whose exported id already exists".

*Too weak*: it is bare-id EXISTENCE, not identity, so a foreign row whose id merely did not collide
walked past it and REOPENED a local `fixed` card by fingerprint — measured, a peer's `CB-9001` flipped a local `CB-1` from `fixed` to `open`.

*Too strong*: every tracker numbers CB-1, CB-2, …, so a foreign export lost every row whose NUMBER
was taken locally (measured: 3 peer rows into a 3-row tracker, 0 landed, reported as "3 already
present"). And because `export-csv` orders by SEVERITY rather than id, ids MINTED BY THE IMPORT
ITSELF collided with later rows of the same file, so restoring a backup into an EMPTY tracker
silently dropped rows (measured: 3 out, 2 back, exit 0).

That the id half could not simply be deleted was proved by review rather than assumed.

**Item (8), the incident that made severity monotonic.** Dedup froze *every* column at first report
while the newest data lived only in the ring: a card filed `low` and re-observed `critical` stayed `low` and was invisible to `query(severity="critical")`, the primary read path. **Item (8), the parameter-ordering hazard as it actually arose.** `meta = ?` used to be spliced outside the built `sets` clause with its parameter appended after the builder finished, which was harmless only while `status = 'open'` — a literal consuming no parameter — was the sole extension. CB-52 added the first parameter-consuming one, so `meta` moved INTO the builder. The measurement
that settled the argument is that one extra column had needed four separate prose warnings.

**Item (8), why milestone routing was left alone.** `stream/security` has `total_items: 0` for the
tracker's whole life, so the routing symptom has never once occurred.

**Item (10), ratification and scope.** Ratified 2026-08-20; behaviour unchanged — the freeze was
already the code, and T-11 only declared it on every reader.

**Item (11), ratification and precedent.** Ratified 2026-08-20. The precedent for unconditional
response keys is `claims._response`, together with the two response-only keys already beside it.

**Item (12), ratification and the golden's movement.** Ratified 2026-08-24 (T-59). CSV import
already handled the identical situation by stripping rather than refusing, which is what made
stripping the right answer for `add` too. The wire golden moved to match the two tool descriptions
— legitimate because a tool description is an INPUT to the schema, not the gated response shape.

**Item (13), a defining clause that was wrong about its own member (CB-247).** The opening clause
once read "one observation-time invariant", and was therefore wrong about the very member it was
introducing: `escalate` and `promote_tags` do sit on the observation path, but `authored` sits on
the UPDATE path — which the same item then said in its own words, both by calling it a SERVICE
write and by contrasting it with its two siblings, so the paragraph contradicted itself without
ever leaving its own bullet.

**Item (13), why a grep over the goldens answers a different question.** Measured 2026-08-28:
`tests/golden/mcp_schema.json` carries the word `authored` once as ordinary prose inside `query`'s
description (the authored-versus-ring meta distinction), and that defeated the first draft of the
sentence, which claimed zero occurrences in either golden.

---

### CB-218 — the upward walk is three-valued {#cb-218-обход-вверх-трёхзначен}

**Justifies the rule** "THE UPWARD WALK IS THREE-VALUED TOO", `CLAUDE.md` → `## Code rules` → `### Database`.

**The measurement.** On the unfixed tree, with the execute bit off a directory that HOLDS the
project's tracker and an unrelated `.codebugs/` one level above it, `codebugs where` printed a clean binding to the stranger at exit 0 with no warning, and `stats` answered about the stranger's empty
population.

**The `.git` half, reproduced in isolation.** An unanswerable `.git` reads as *no boundary here* and
the walk crosses the repository boundary. It was reproduced in ISOLATED form — the repository
directory fully readable, its `.codebugs/` provably absent, and a symbolic-link loop at `.git` — so
nothing else could be blamed.

**A truncating version of the unexamined list was written first and refuted by measurement.** One
wall makes every question below it unanswerable too, the list runs deepest-first, and a cap keeping
the first entries kept the wall's shadows while dropping the entry naming the wall itself.

**Why the `_enclosing_worktree_root` sentence is written in the past tense (CB-239).** When CB-218
landed, that function took the same primitive with NO behaviour change — said that way round because
no test could discriminate it — since it then only chose between two refusal sentences and was
reached only after the walk had already recorded the same prefix. CB-227 later made it RETURN the
third value instead of dropping it, so it no longer merely picks a refusal sentence; while both
sentences stood in the present tense the document said the opposite of itself two bullets apart and
a reader could not tell which half held today. Every claim about live code is now written so its
as-of is visible.

**What the preflight replaced (CB-11).** Before it, a misconfigured server looked healthy at startup
and failed every call forever, with no single moment naming the cause.

---

### CB-86 — an environmental sqlite failure {#cb-86-средовой-отказ-sqlite}

**Justifies the rule** "An ENVIRONMENTAL sqlite failure is classified inside `_open`", `CLAUDE.md` → `## Code rules` → `### Database`. **Why it was missed three times.** CB-71's `open(` sweep and CB-79's `OSError` widening were both structurally blind to `sqlite3.OperationalError`, because it is not an `OSError`. The class of "the
CLI crashed at an I/O boundary" was closed three times without anyone enumerating the family.

**The rejected design is the instructive part.** Adding a `sqlite3.OperationalError` arm at `cli.main` was refuted by this repo's own `tests/test_bench.py:789`, which ratifies the traceback as
the discriminator between a post-commit failure and an input error — a central arm cannot tell those
apart, which is verbatim CB-55's constraint applied to a different exception class. The "exit code
is unchanged so no new lie is possible" argument **proves too much**: it would equally license the
central `except OSError` CB-55 forbids.

**Why one classification point once sufficed for a read-only database FILE as well, and no longer
does (CB-199, CB-213).** Three raise sites live inside `_open`, and one of them is `merge.ensure_schema`, several frames down through the `_resolved_order()` loop — verified by running it. CB-195 made that seed write conditional on a read, so once the seed rows exist `_open`
attempts no write of its own and the failure surfaces later, at the domain's own INSERT. The
sentence in the rules layer is deliberately in the past tense so the two bullets cannot be read as
disagreeing.

**Why the two absences were measured rather than reasoned.** A sibling card had added three
prose-sourced entries to the code set, in the paragraph congratulating itself for avoiding exactly
that.

---

### CB-17 / CB-21 — INSERT/UPDATE column parity {#cb-21-паритет-колонок}

**Justifies the rule** "A column settable at INSERT should be settable at UPDATE",
`CLAUDE.md` → `## Code rules` → `### Database`.

**What this bullet used to say, and why it was replaced.** It read *"this rule is currently violated
… read it as a target with a known outstanding debt"*, because `update_finding` reached only `status, severity, tags, meta, reported_at_ref` while `update_requirement` could already rewrite `description` — the identical asymmetry as CB-17 — and `source` was INSERT-settable on **both**
entities yet appeared in neither update contract. Nothing anywhere stated the intended matrix.

**The card's own premise was corrected by the work.** CB-21 recommended making `file`/`description`
mutable because "there is no integrity argument for freezing" them. There is one: they are inputs of
the derived `auto:v1` fingerprint, so writing them would re-key identity. CB-61 later negotiated exactly one such operation, `normalize_categories`, which issues its own UPDATE for precisely that
reason.

---

### CB-25 — "no filter" is not truthiness {#cb-25-пустой-фильтр}

**Justifies the rule** «"No filter" is `None` and `""`», `CLAUDE.md` → `## Code rules` → `### Database`. **What the write-side-only sweep found, with the detail of each case.** `merge.get_sessions` had a `types.MERGE_STATUSES` that was **dead code**, so the CHECK constraint was the only enforcement; `milestones.list_milestones` had `MILESTONE_KINDS`/`MILESTONE_STATES` all along and simply never consulted them on query; and `blockers.query_blockers` had its `TRIGGER_TYPES` check sitting
*inside* the truthy guard, so a falsey value skipped it entirely.

**Why the first sweep missed one.** It grepped `if status:|if severity:|if priority:|…`, an enumeration of the filters already known, and therefore could not find `trigger_type`. That is this
repository's recurring lesson in a new place: a rule expressed as an enumeration is the letter, and
the letter cannot decide.

---

### CB-45 — the similarity extension {#cb-45-расширение-похожести}

**Justifies the rule** "Similarity extension (CB-45)", `CLAUDE.md` → `## Code rules` → `### Database`. **How `DEFAULT_THRESHOLD = 0.7` was calibrated.** On the 3162-row autosorter corpus it collapses 102
rows into 11 coherent families and splits the 115-row gate category into its ~10 genuinely distinct
failure tails. The CB-45 card's proposed 0.95 was measured and REJECTED: 77 rows, and the target
family never unifies — unifying it would be the false merge CB-43's RISK section forbids. The owner
was notified per the letter-fix protocol.

**Two findings from one Codex diff review.** The empty-category pool defect — every empty-category
observation pooling the whole table — and, in the same round, `group_report`'s bare `status == "all"` sentinel test, which was replaced with a type-pinned one because of CB-25's `mock.ANY` trap.

**The registry-read exemption was a same-day review finding.** What it found was a hardcoded key
name in findings, i.e. core knowing an extension's vocabulary.

**What the diameter measurement is worth.** The corpus's 43-row family hides a 0.392 pair behind
0.7-plus edges, which is why `min_pair_score` is computed over all member pairs rather than over the
recorded edges.

---

### CB-60 / CB-61 — category normalization and the retro-fold {#cb-60-нормализация-категорий}

**Justifies the rule** "Category spelling is normalized and MINTING a new category is gated",
`CLAUDE.md` → `## Code rules` → `### Database`. **The measured identity fork that motivated it.** `process-improvement` versus `process_improvement` — two spellings of one category, hashing to two fingerprints.

**A sentence that stopped being true, and how.** The bullet used to close on "deliberately left
open" after saying the ADD path never rewrites a stored row. CB-61 built the retro-fold as a separate
and explicit operation, so the clause was replaced rather than left to rot.

**What actually happened on this tracker, and in what order.** The separation of code from decision is
the rule, and it is also the history: the run was ratified as its own decision after the code had
landed, and this tracker's corpus was folded once — **17 rows, no collisions, nothing unverifiable** —
which is why it no longer carries variant spellings.

---

### CB-8 / CB-23 — what counts as a tracker {#cb-23-что-считается-трекером}

**Justifies the rules** "DB discovery", "What counts as a tracker differs by how you got there" and
"Discovery is a heuristic, so it has a declared override" — `CLAUDE.md` → `## Code rules` → `### Database`. **The asymmetry was not a free choice between semantics.** `_db_path`'s own docstring already promised the named branch would refuse, while the code checked only `os.path.isdir` — so the fix
brought the code up to a contract that had been written down and never honoured.

**A contradiction this bullet once asserted against itself.** An earlier draft claimed
"`init_project` is the only creator" and, two sentences later, described `connect` creating on the
walk route. Both halves were in the same paragraph.

**Why `_open` had to be split out at all.** `init` used to create its database *by way of* `connect`,
so tightening the resolver broke the one caller that must create.

## Что в этом файле, и чего в нём нет

**Что в этом файле.** Обоснования правил из корневого `CLAUDE.md`: почему правило появилось, какой
инцидент его породил, что показали раунды состязательного ревю, какие формы были отвергнуты и по
какому замеру. С T-131 сюда же переехала операционная глубина — устройство сторожей и хуков,
пределы алярмов, внутренности гейтов.

**Чего в этом файле НЕТ, и это важнее.** Здесь нет ни одного правила, которое нужно знать до начала
работы. Всё такое осталось в корневом `CLAUDE.md`, потому что этот файл не впрыскивается в сессию —
его открывает только тот, кого сюда послали. Если ты ищешь, как завести рабочее дерево, что значит
код отказа или что можно коммитить на `main`, — тебе не сюда, а в корень.

**Кто сюда ходит.** Тот, кто правит соответствующую подсистему, — и тот, кто собирается ослабить
правило и обязан сперва узнать, чем за него заплатили.

---

# Перенесено из корня юнитом T-131

## Code rules / Database

Standing in a directory is evidence about where you are; a named path is an assertion that can be stale, inherited by an unrelated subprocess, or mistyped — and that is where a silent second empty tracker does the damage CB-8 was filed for. **The benign half matters too**: `init_project` creates the directory *before* the database, so a Ctrl-C'd `init` leaves exactly this state, and the walk self-healing it is the right answer. **"`init_project` is the only creator" is false as a flat statement** — `connect` creates, by design. 

There is no local discriminator: git reports that directory as a valid work tree too, so any "fix" would be a different guess. 

That default is the whole fix**: three ways to break a tracker were known when the unit started and measurement found five more, each with its own errno, and there is no reason to think eight is the population. 

Verified by running it: `in_transaction` goes `True → False` and a subsequent `ROLLBACK` finds nothing to undo. A gate that says "you hold the lock" before the lock is committed is worse than the defect being fixed.

An earlier design added a `TxnAbort` sentinel for this and was rejected in review: `db.txn` deliberately swallows a failed `ROLLBACK` (correct, so cleanup never masks the real exception), which would have let a refusal-shaped result come back with the transaction still live. 

Round 1 sampled it at the top of the function, before the write lock and before the injected git callback. Round 2 moved it below those — and left the stale-holder `abandoned` UPDATE between the sample and the write. Each time the lease landed **already expired**, the call returned `proceed: True`, and the next contender saw the lock reclaimable and *also* got `proceed: True`: two agents merging at once, which is the one thing the lock exists to prevent. 

So an expired holder retrying got `proceed: True` from the first branch while a competitor reclaimed the lease and got `proceed: True` from the second — two agents pushing to main, defeating the singleton lock without ever racing inside it. Every existing test used a *fresh* lease, where the two branches agree. 

A mechanical sweep (`grep -rn "conn.commit()"` → 43 executable sites, vs 7 `db.txn` users, then read every committing function) found **19 instances, 13 still unfixed** — in `blockers.py`, `merge.py`, `sweep.py` and three milestone modules no card had named. 

The rule survives as the reason it was done that way — making the update path lenient while insert stayed strict would have created a worse, same-field inconsistency, so the seam had to move in one step rather than per-site. 

`bench.import_csv` did exactly that with `date or utc_now()[:10]` and `run_id or _next_run_id(conn)`, so `date=[]`, `date={}` and `date=""` all stored today's date and reported success (measured; **the card itself got this wrong**, claiming the dict cases raised — `{}` is falsey and took the same silent path). 

Nor are the remaining 23 held by it: 21 are in `findings.py`, whose single use of the validator is the import-time check over `_RESTORE_COLUMNS`, and that query is none of the 21. **This is NOT a security hole, and that was re-established rather than inherited**: all 52 sites were walked at the cut above and every interpolated expression traced to its assignment — 30 distinct ones, each a locally built clause fragment, a module constant, a literal picked by a Python conditional, a `?`-placeholder string, an integer from `len()` of a module constant, or a closed-enumeration member checked before interpolation. On the cut that matches what CB-172 must leave green — ruff `0.15.7`, `extend-select = ["S608", "RUF100"]`, over the `src/ tests/` that `worktree-finish.sh` lints, on `adcf354` — it is **56 dead markers beside those 56 `S608` hits**, 17 in `src/` and 39 in `tests/`; the same tree answers 93 when `select` REPLACES the default set instead of extending it. **The error corrected here was this paragraph's own kind** — *the tool is assumed to work although nobody ran it* — committed in the sentence that exposes it, which is why every number above names the rules enabled, the paths walked, the ruff version and the commit. Before CB-22 the comment claimed all three were guarded and only `sort_col` was, inside `order_by()`; a kind carrying `readable_cols={"(SELECT meta FROM findings)"}` passed the membership check and `field()` returned the `meta` column. 

## Claims module

It is
  **deliberately distinguishable from `1`** — that distinction is the whole reason the alternative
  "silent exit 0" was rejected, since a `codebugs export-csv /dev/stdout | gzip > backup.gz` whose
  `gzip` dies must never report success over a truncated backup. It replaces the two codes that state produced before it (`1`
  unbuffered with a raw traceback; `120` block-buffered with "Exception ignored while flushing
  sys.stdout"), the first of which is this package's code for **bad input** printed over a landed
  write — the CB-15/CB-16 lie. 

One detail worth carrying to any third adopter: **the fatal/guarded asymmetry is
  about WHEN, not about importance** — setup may abort because nothing has been created yet and a
  refusal is free, while finish runs after the merge has landed, where a false failure over tracker
  bookkeeping is the worse outcome.
Ratified by
     the owner, 2026-08-19, against the design doc.
The design's own text says why
     the difference is correct: there, `[7b/9] auto-resolve-codebugs.py` has already flipped the card to `fixed`
     from a `Fixes:` trailer, so the release is a no-op. 
