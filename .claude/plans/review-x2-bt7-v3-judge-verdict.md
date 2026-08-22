# BT-7 v3 — JUDGE VERDICT (adversarial-review-x2, round 3)

Target: `.claude/plans/BT-7-location-anchor.md` (v3, commit `88ee00a`)
Measurement record: `.claude/plans/BT-7-MEASUREMENTS-2026-08-22.md` (commit `c208c47`)
Attackers: Opus (`review-x2-bt7-v3-opus-attack.md`, 6/10, 3 FATALs) and Codex/GPT-5.6-Sol
(`review-x2-bt7-v3-codex-attack.md`, 7 FATALs, confidence 0.97).
Defender: `review-x2-bt7-v3-defender.md` (53 CONCEDE / 13 PARTIAL / 2 DEFEND).
Holder's own re-run: `BT-7-v3-holder-verification.md` (three findings verified personally).
Judge: Opus, read-only, 2026-08-22. No tracker write verb issued, no `tools/` touched, nothing
committed.

**What I verified myself rather than relaying.** Both DEFENDs (against code, below); the internal
contradiction that drives FATAL-2 (v3's own §1а, five rows above §2.1's claim); the cost arithmetic
(§1а's table against §7.4's gate); `add_finding`'s signature and the resolver observation literal
(`findings.py:1200-1216`, `:1058-1071`) — no `project_dir`, no root, no channel that could carry
one; `_ENVIRONMENTAL_CODES = frozenset({8, 10, 13, 14})` (`db.py:483`) with `SQLITE_BUSY = 5`
outside it; and that `_add_one` — hence `run_pre_add_resolvers` — runs inside `db.txn` on **both**
entry points (`findings.py:1273`, `:1860`). Corpus counts I did **not** re-derive: three
independent parties (Opus, Codex, defender) plus the holder reproduced them today, and the finding
that matters does not need a corpus at all.

---

## Verdict summary

**NOT RATIFIABLE as written. Fix six things, then send it — and do not run a fourth x2.**

Six items block an owner decision (list below). None of them is a design defect; all six are
places where the document tells the owner something the document's own measurements contradict, or
declines to tell him something he is being asked to authorise. That distinction is the whole
verdict: **the design is close to ready and the ratification instrument is not.**

**Trajectory ruling: CONVERGING on structure, NOT converging on the number→claim seam — and those
must be scored separately, because treating them as one score is what makes v3 look like
thrashing when it is not.**

*Converging, and hard.* Take the two attackers' own closure audits rather than their headlines.
Codex marks 22 of 35 round-2 rows CLOSED by mechanism; Opus's per-axis scorecard runs storage
2→3→**9**, premises 3→8→**9**, census fidelity 2→8→**10**, grammar 2→6→**8**. Both attackers, from
different families, independently call §1а the best measurement work the design has carried, and
every census cell reproduces exactly on three independent exports. The five decisions that make up
the design — content anchor as core, resolve-on-read, anchor outside identity, storage in top-level
`meta.loc`, capture in the existing CB-45 resolver — took **zero** attacks in round 3 from either
family. That is not a document going in circles. Round 1 was a rebuild order; round 3 is a fix
list; the distance between those two is the whole point.

*Round-3 findings are majority NEW GROUND, and the mechanism is worth naming.* The path-cache
lifetime, the in-repository read oracle, the wall-clock bound, `anchor_recapture`'s failure
semantics, the two contradictory ambiguity algorithms, the basename-vs-suffix gate — **none of these
surfaces existed in v2**, because v2 had a two-phase carrier, C2 in the cascade and ring storage in
front of them. You cannot attack the inside of a room you have not entered. A design that keeps
exposing new surface under attack is advancing; a design that keeps re-exposing the same surface is
thrashing. This one is doing the first.

*Not converging, in exactly one place.* The round-2 headline charge — *honoured in letter,
falsified in fact* — **recurs, for the third consecutive round, and it recurs in the ★ cell.** It is
one defect class with three instances in v3: the capability figure (`2907/3300` is a `file`-column
census presented as an anchoring capability), the cost share (`2–8%` is one cell of a four-cell
table), and the batch premise (`probe_cost.py` **`print()`s** its conclusion instead of computing
it). Add the unpaid round-1 condition (Т-c, third round) and the calibration corpus, and the shape
is unmistakable and the holder has already named it better than either attacker did: *«перемерить
ЧИСЛО мало — надо перемерить ПОПУЛЯЦИЮ, на которой оно взято, и ОПЕРАЦИЮ, которую оно якобы
описывает.»*

*So the process ruling, which is the part the holder must act on.* Three rounds of trying harder
have not closed this, and a fourth will not either — this is the repo's own ratified lesson landing
on its own review process: **prose is the wrong enforcement layer for a defect whose defining
property is invisibility from inside one section.** The mechanical form is cheap: **one NUMBERS
table in the document, in which every owner-facing figure appears as
`<numerator>/<denominator> under predicate P, export <sha256>, <row count>, <date>` — and any
figure in the prose that is not in that table is a defect by construction.** All three v3 instances
die on contact with that rule: `2907/3300` would have had to write its predicate ("the `file`
column is a readable regular file"), and writing it is seeing it. That table is worth more than a
round 4.

**Do not run a fourth x2 review.** Both families' round-3 output converges on a fix list, not on
the design; the marginal finding is now cosmetic while the marginal cost is another two days and
another corpus drift. What v4 needs instead is a **narrow numeric audit** against the NUMBERS
table — and the evidence that this works is in this round: the holder's own K-5 re-run caught the
population error, the corpus contamination *and* an optimum neither attacker found (short anchor,
wide context). The holder is now a better instrument on numbers than a third attacker is.

---

## Corroborated by both models

High-confidence by construction: two model families, independent runtimes, independent
re-derivations. Thirteen rows.

| # | finding | Opus | Codex | ruling |
|---|---|---|---|---|
| 1 | **Anchorable population is ~20% / ~34%, not 88% / 97%** — the claim is the `file`-column census, and anchoring also needs a *line* | FATAL-2 | F1 | **UPHELD.** Also confirmed by the holder, and by the defender at 656/3303 and 47/138. The refuting number is **five rows above it in v3's own §1а table**. Owner-blocking. |
| 2 | **Capture has no worktree-root channel** — `add_finding` has no `project_dir`, the observation literal carries no root, deriving from the connection yields main's checkout | FATAL-1 | F2 | **UPHELD, verified by me in code.** The read side (Р5) is genuinely fixed; F-3 was closed on one side and reopened on the other. Owner-blocking, because "ноль правок сигнатур ядра" is one of three things the owner is being sold. |
| 3 | **v3's own §7.5 pessimism fails v3's own §7.4 landing gate** — +100 ms on a 125 ms batch is +80%, on 303 ms is +33%, against a 25% gate | SERIOUS-1a | F4 | **UPHELD, arithmetic re-derived by me from §1а.** The "2–8%" headline is the pool-600 cell; the same two capture numbers give 13.3–46.6% at pool ≈0. See the composition finding below, which changes this materially. |
| 4 | **The batch path-cache claim is unspecified and its premise is asserted, not measured** | SERIOUS-5 | F5 | **UPHELD in substance, one half dismissed.** `probe_cost.py`'s last two lines are literal `print()`s of the conclusion; the measured distinct-path ratio over real filing groups is **56%**, not "few". Codex's *"not implementable"* is dismissed — see Dismissed. |
| 5 | **Basename equality is not corroboration** — `src/a/server.py` and `src/b/server.py` pass the gate; CB-2944 is a live counterexample (`core/entities.py` vs `cli/commands/entities.py`) | SERIOUS-9 | S8 | **UPHELD.** Found independently with the same corpus reproduction. Cheap suffix-match fix, cost measured: 2 refused, 16 kept. |
| 6 | **Unbounded lock time on regular files** — `S_ISREG` closes two enumerated members of a class whose survivors are all regular; a 5 s block turns concurrent adds into raw `sqlite3.OperationalError` | SERIOUS-2 | F7 | **UPHELD, chain verified by me:** `busy_timeout=5000`, `SQLITE_BUSY = 5`, `_ENVIRONMENTAL_CODES = {8,10,13,14}` — never converted to `TrackerUnwritableError`. *(The brief attributed lock-monopolization to Codex alone; that is not right — Opus SERIOUS-2 is the same finding, reached independently.)* |
| 7 | **`realpath`-then-`open` is still check-then-open** | SERIOUS-6 | S2 | **UPHELD.** v3 delivers the `fstat` half; containment is still decided from a pathname. Closable in stdlib, so "declare it as half a boundary" is the floor. |
| 8 | **Resolver exceptions become `resolver_errors`, contradicting §4.3's promise** | SERIOUS-4 | S1 | **UPHELD, and the defender found a residue neither attacker did:** `_validate_resolver_outcome` runs *inside* the runner's own swallowed `try` (`db.py:436`), so no amount of catching inside `loc.py` can keep the promise. |
| 9 | **The cost probe does not measure the specified capture** — `capture_once` is open+read+decode+strip+2×sha256; no `realpath`, `stat`, NUL scan, indent token, grammar or root discovery | W-10 | F3 | **UPHELD.** Two independent runtimes measured the single omitted dominant item, `git rev-parse --show-toplevel`: **1.264 ms** (Codex) and **1.064 ms** (defender) — 5.5× the *entire* claimed 0.192 ms capture. |
| 10 | **`MAX_BYTES_READ` derived on the wrong population** | FATAL-3c | D1 | **UPHELD, and the two families found it from opposite ends** — Opus from the referenced-file population (4/2919 = 0.14%, 7× the quoted rate), Codex from the excluded extension (`.jsonl`, a live 25 MB reference the census never sees). |
| 11 | **`unreachable_commit` is outside the "closed" §4.3 dictionary** | N-4 | closure audit | **UPHELD.** Cosmetic, but it is a totality claim in a document that ratifies totality claims. |
| 12 | **MCP/CLI contract incomplete** — outer envelope, missing-id behaviour, filter defaults, CLI output and exit codes | CA | M7/MR12 | **UPHELD — and explicitly NOT owner-blocking.** See Recommended. |
| 13 | **`anchor_recapture` is a named verb with no failure semantics** | W-6 residue | S9 | **UPHELD.** It decides whether the repair path can destroy the last good anchor, and whether `loc.py` stays zero-SQL like `similarity.py`. |

---

## Cross-model disagreements

### What the second family bought that the first did not

**Codex alone found the one finding in round 3 that ADDS AN OWNER QUESTION rather than correcting
an answer** — F6, the in-repository file-read oracle. Any MCP `add` caller can name
`<root>/.git/config`, an untracked `.env` or a credentials file: it is inside the root, regular,
546 bytes, and passes every stated predicate. The normalized bytes are then persisted in
`meta.loc.text` **and returned in the add response** — the defender traced the whole path
(`findings.py:2988-3001` → `_finalize_add` → `SELECT *` after the resolver patch merged), and
`meta.similar_to` already arrives by exactly that route. §7.3's owner question asks only about
paths **outside** the worktree, so the capability the caller actually gains is not on the page.
That is a trust-boundary decision and it is the owner's alone. Opus's SERIOUS-7 (`text` has no
reader) points at the same field from the other side and never reaches the capability.

Codex-only, secondary but real: **S7** — "популяция записи — каждая ВСТАВКА" is false (the firing
predicate is `finding_id is None and annotate`, so explicit-id inserts, import and restore get
neither an anchor nor a refusal object, and every lost-rate denominator inherits the error);
**S5** — Р7's prose and table give two contradictory ambiguity algorithms, and zero-survivors-after-
context has no row at all; **S4** — `indent // 4` and whitespace collapse make Р6's *own justification
sentence* false below a 4-column step; **S3** — §5's C2 trigger is not computable from
`anchor_resolve`'s output, which is the trigger for the mechanism that would compute it; **M5** —
grammar fallback (invalid higher-priority key vs valid lower key) is genuinely unspecified.

**Pattern, unchanged from round 1: Codex is stronger on contract completeness, capability surface
and alternatives.**

### What the first family bought that the second did not

**Opus alone found the calibration-corpus contamination**, and it is the most consequential
single-family finding of the round because it **overturns constants the document names as
ratifiable.** The 52 261-file uniqueness corpus that sets `CONTEXT_LINES`, `MAX_ANCHOR_LINES` and
`MIN_ANCHOR_CHARS` is **93.2% vendored, CI-runner and build trees** — `actions-runner/_work` 46.0%,
`.venv312/lib` 40.9% — because `probe_calibrate2.py`'s `SKIP` set holds `.venv` and not `.venv312`,
`venv`, `actions-runner` or `build`. First-party is 6.8%. Three parties re-ran it and got the same
composition. Consequences Opus then measured: the "измеренный пол неоднозначности 2.46–3.82%" that
§10.1 promotes to a first-class design fact is **0.38–1.17% on first-party source**; and
`CONTEXT_LINES = 3` was **never swept at all** (`CTX = 3` is a hardcoded module constant, the loop
sweeps `span` only).

**And the holder's own re-run then went past both attackers**, which is worth recording because it
is the round's best single argument: context width is **free in storage** (two integer widths and
one hash, at any width) while anchor span is **paid forever in `text`, in every `SELECT *` and every
export**. So the design tuned the expensive dial and froze the cheap one. His measured sweep on
3 552 first-party files: `span 1 / ctx 5` = 0.36% beats `span 5 / ctx 3` = 0.40% while storing one
line instead of five, and `span 1 / ctx 8` reaches **0.11%** — 35× better than the "irreducible
floor" v3 asked the owner to accept. Neither attacker made the storage-asymmetry argument.

Opus-only, secondary: **W-1** (§4.1's branch table does not partition — but see Dismissed for the
one sub-claim that is wrong); **SERIOUS-8** (the load-bearing dialect rule is 86% in autosorter and
38% in codebugs, read as one agreeing fact — and codebugs is the tracker whose owner is ratifying);
**SERIOUS-7** (`text` is the largest field, the whole of §2.2's byte-cost row, and has no reader in
any algorithm in the document); the filing-group distinct-path measurement that refutes the cache
premise.

**Pattern, unchanged from round 1: Opus is stronger on measurement against the live corpus and
against this repo's own ratified history.**

### Where the attackers contradict each other

1. **The path cache.** Codex: *not implementable*, restore a two-phase `CaptureContext`. Opus:
   implementable but unspecified. **Opus is right, and the difference is load-bearing** — Codex's
   remedy would reverse the single best decision of the rebuild. A module-level cache keyed on
   `(realpath, st_mtime_ns, st_size)` needs no seam change, is correct across batches, worktrees and
   long-lived servers, and reuses the `stat` the `S_ISREG` check already performs.
2. **The cost share.** Codex quotes 1.2–46.6% (pool 1200 for the low end), Opus 13–47% at pool 0 and
   2.3–8.1% at pool 600. Both endpoints are arithmetically correct; the honest statement is a
   per-pool range and neither summary.
3. **The `locs:[…]` shape.** Codex: STILL OPEN. Opus: deferred with a trigger, acceptable. **Split:**
   the deferral is fine, the word *«единственная»* in §2.2's (c) row is not.
4. **Malformed vs forged manual anchors.** Opus rules it a legitimately declared limit; Codex found
   the narrower case the declaration does not cover — a *well-formed forged* `loc` is read as
   `current`. Both are right about different things; §10.2 needs one clause.

---

## Mandatory fixes before the owner sees this

Six. Each blocks a **ratification decision**, not an implementation.

1. **Replace §2.1's capability cell and §8 q1's cost line with the anchorable population, stated in
   both framings.** The owner is being asked to authorise a feature on an 88% capability figure
   whose true value is ~20% (autosorter) / ~34–47% (codebugs) — and the honest presentation is
   double, because both halves are true and each answers a different question: *of the cards that
   name a location at all, ~95% get an anchor; of all cards, ~20%.* Show the three-way split (no
   locational key / unparseable value / unreadable file) and the upper bound beside it, and stamp
   the export with a hash and row count — the corpus moved 129 → 131 → 138 during this review.

2. **Name the capture root, or tell the owner the "zero core signature changes" headline does not
   survive it.** The seam carries no root and structurally cannot; the process cwd is not the
   worktree (`provenance._ambient_cwd`'s own docstring is about precisely this failure); deriving
   from the connection yields main's checkout — which is verbatim the divergence v3 cites as its
   reason to reject `describe_root()`. This changes the *price of "yes"*, and the owner must read it
   in v3 rather than discover it in Т-a.

3. **Put the in-repository read capability in front of the owner as its own question, or delete
   `text`.** q3 asks only about paths *outside* the worktree; what any MCP `add` caller actually
   gains is the ability to have `.git/config` or an untracked `.env` read, normalized, persisted and
   **returned in the add response** — a capability expansion, not a scope detail, and therefore not
   something a holder may decide on the owner's behalf. Deleting `text` closes the payload, most of
   §2.2's byte-cost argument and Opus SERIOUS-7 in one line, and is the cheaper answer if `text` has
   no reader.

4. **Re-derive the constants on first-party source and sweep both dials, because the document names
   them as ratifiable.** `CONTEXT_LINES = 3` was never swept; the corpus behind it is 93% foreign;
   and on the holder's own first-party sweep a **short anchor with wide context dominates on both
   axes at once** — better uniqueness *and* fewer stored bytes per card forever. Do not simply adopt
   `(1, 8)`: state what the winning pair costs in **edit-robustness**, the axis no sweep in this
   review measured, or the design repeats the error of tuning one dial on one metric.

5. **Tell the owner that "yes" means the tracker's write lock is held across a filesystem read with
   no time bound today.** `S_ISREG` closes two enumerated members of a class whose survivors are all
   regular files (hung NFS/FUSE, autofs, `realpath`'s per-component `stat`s), and the measured
   consequence is not a degraded anchor but *other agents' adds becoming raw tracebacks* —
   `SQLITE_BUSY` is 5 and `_ENVIRONMENTAL_CODES` is `{8,10,13,14}`. One sentence of disclosure plus
   a committed wall-clock budget and a `timeout` token; **do not** reverse Р3's placement to get it.

6. **Make Т-c required, or state in §5 that every trigger is computable over new inserts only.**
   This was the judge's round-1 condition **attached to** the concession the owner is being asked to
   ratify (insert-only capture), it is open for the third consecutive round, and taking a concession
   while dropping its condition is precisely what the owner's standing rule forbids: the letter
   honoured, the intent quietly dropped.

---

## Recommended fixes

Implementation-grade. **None of these blocks an owner decision**, and carrying them into an owner
conversation is the specific failure mode the holder has been warned about. They belong in Т-a/Т-b
briefs and in the v4 diff, not on the ratification page.

**Correctness of mechanism (Т-a):** give `path` an invariant (string, relative, traversal-free,
bounded) — it is the one field that selects the file that gets opened and the one field with no
rule; add `internal_error` to §4.3 and note the residue no `except` in `loc.py` can reach
(`_validate_resolver_outcome` runs inside the runner's swallow); add the "capture never raises"
test; specify the path cache's scope, lifetime, invalidation key and memory bound
(`(realpath, st_mtime_ns, st_size)`); replace basename equality with a suffix match when the token
carries a directory (measured: 2 refused, 16 kept); fix §2.2's "популяция записи — каждая ВСТАВКА"
and every lost-rate denominator that inherits it; give `context_before`/`context_after` invariants;
resolve Р7's prose-vs-table ambiguity contradiction and give zero-survivors-after-context a row;
define `captured_at_commit` and name its reader; make `MAX_TEXT_BYTES` 2000 or state why 2048;
specify grammar fallback for an invalid higher-priority key.

**Contract completeness (Т-b):** `anchor_resolve`'s outer envelope, pagination, missing-id
behaviour, CLI output and exit codes; the CB-19/CB-25 vocabulary-filter and empty-filter
conventions for `status`/`category`; `anchor_recapture`'s transaction phase, whether transient
failure may replace a good anchor, whether it overrides `loc: null`, and whether it writes through
`update_finding(meta_update=)` or its own SQL.

**Honesty of the document, not of the decision:** publish the cost share as a per-pool range;
re-baseline the §7.4 gate on the landing machine or replace it with an absolute per-member budget;
`MAX_BYTES_READ` re-derived on the referenced-file population; `MIN_ANCHOR_CHARS` at finer bucket
resolution or declared as "any value in 20–29"; §4.1's table must partition (fix `95+3`'s notation,
add codebugs `sites` B3 = 6, stop counting the dict-`sites` rows in both B3 and B5); stop pairing
86% with 38% as one agreeing fact; drop *«единственная»* from §2.2(c) and name the unevaluated
bounded-`locs[]` shape; fix Р6's reindent claim; say §7.6's two-read check is not in Р7, or add it;
say whether resolution re-applies containment to a stored, caller-writable `path`; `out_of_repo`
collides with `provenance.py:345`'s payload-carrying token; `sites_dropped` conflates other-places
with truncated-lines; name the offline probe that computes §5's C2 trigger; say the request was
narrowed (CB-95 asked for auto-resolution and both auto surfaces were deleted for correct reasons —
that belongs in a question, not a §10 bullet).

**Artifact hygiene (measurement doc):** fix `SKIP`; fix the normalizer (`r'\\s+'` matches a literal
backslash, so internal whitespace is never collapsed — measured effect nil, but the script does not
implement the spec it calibrates); disclose the sampling phase (measured bias ~0.1 pp, on the
conservative side); stamp every export with `sha256` + row count + timestamp; **delete the two
`print()`s in `probe_cost.py` that assert a conclusion the script does not compute** — that one is
not hygiene, it is the round-2 charge in artifact form, and it is how the claim got into the
document.

---

## Dismissed findings

| finding | ruling | reason |
|---|---|---|
| **Opus W-1's sub-claim: the 3 non-int `line` values are B4** | **REFUTED — verified by me** | v3's own B1 row reads *«голый `int` или int-строка»* (`BT-7-location-anchor.md:394`); the three values are the int-strings `"51"`/`"13"`/`"45"` (CB-1886/1875/1873). `95+3` is a type split **inside B1**. The fix list must not move them. The rest of W-1 stands. |
| **Codex F5's "cache by path per batch is NOT IMPLEMENTABLE"** | **REFUTED as stated; substance kept** | A module-level cache keyed on `(realpath, st_mtime_ns, st_size)` needs no seam change and is correct across batches, worktrees and long-lived servers. Dismissed specifically because Codex's remedy — restore a two-phase `CaptureContext` — would reverse the rebuild's best decision. The real finding is Opus SERIOUS-5: unspecified, not impossible. |
| **Round-1 mandatory fix #2 ("capture OUTSIDE the lock") should be re-imposed** | **DEFEND UPHELD — verified in code** | `similarity._annotate_resolver` (`similarity.py:349-369`, registered `:376-381`) already runs **per member inside the same `BEGIN IMMEDIATE`** — I confirmed `_add_one` → `run_pre_add_resolvers` (`findings.py:1058`) sits inside `db.txn` on both entry points (`:1273`, `:1860`), and that resolver is what the 8.24/16.23 ms add cost is *made of*. Adding 0.19–0.67 ms of CPU there is not CB-31's defect; **unbounded blocking time is**, and that is a wall-clock budget, not a relocation. The reversal was legitimate; its supporting arithmetic was not, and that is what mandatory fix 5 addresses. |
| **Opus SERIOUS-1b: "the form of the argument is CB-31's defect"** | **DISMISSED** | CB-31 was per-row work added to a **read** path that had none. Same reasoning as above. The charge that survives is SERIOUS-2. |
| **Codex D5: "the corpus is mutable and unauditable"** | **MOSTLY DISMISSED** | Overstated: the scripts are committed verbatim and **three independent parties re-ran them today**, reproducing every §1а cell modulo drift and §6.2's uniqueness table to the digit. Residue kept and folded into mandatory fix 1: stamp the export. Do not re-litigate reproducibility — it is the strongest thing v3 built. |
| **Codex D3: every-20th sampling is systematic** | **DISMISSED as decision-changing; kept as hygiene** | Measured across all 20 phases: ~0.1 pp absolute, and phase 0 sits at the *bottom* of the range — the published figure is biased **conservative**. Right in kind and direction, wrong in importance. |
| **Codex S4: "the normalizer reports edited code as unchanged"** | **PARTIAL — mechanics upheld, conclusion dismissed** | Whitespace collapse is the *intended* robustness of a normalizing anchor; matching through a formatting-only edit is the feature, not the bug. What is falsified is Р6's justification sentence, which holds only when a reindent crosses a 4-column boundary. Fix the claim, not the normalizer. |
| **Codex S3: the C2 trigger is uncomputable in principle** | **PARTIAL** | Correct that it is not computable from `anchor_resolve`'s output (`lost` carries `reason=null`). Not correct that it is uncomputable: a one-off offline probe over `captured_at_commit..HEAD` answers it without shipping C2. §5 must name the instrument. |
| **Opus N-1/N-3/N-5/N-6, W-2, W-3** | **UPHELD but cosmetic** | Real, cheap, and demoted to Recommended — a detail must not carry the weight of a load-bearing decision, which is the sorting rule this verdict is built on. |

---

## A composition finding neither attacker could reach

Both attackers were correct section-by-section and **neither connected two of their own findings**,
which is this repo's own ratified lesson — *a check that validates elements cannot validate their
composition* — landing on the review rather than on the code.

**Correcting FATAL-2 partially discharges SERIOUS-1.** §7.4's gate arithmetic assumes a 100-member
batch performs 100 file reads. It cannot: capture reaches the file only for members whose value
parses under §4's grammar, and that is the very population FATAL-2 measured. On v3's own §1а
figures a 100-member autosorter batch reaches the filesystem for **~21** members, not 100 — so
§7.5's pessimistic +100 ms becomes ~+21 ms on a 125 ms batch, **+17%, inside the 25% gate at every
pool depth**. On codebugs' denser dialect (~48%) it is ~+48 ms, **+38% at pool ≈0 and inside the
gate from pool 200 onward.** So the honest statement is not "the gate fails at two of four pool
depths" but *"the gate holds on autosorter's dialect throughout and fails on codebugs' at an empty
pool only"* — a materially smaller and far more defensible claim, and one that must be **measured,
not asserted**; I am giving the shape of the correct computation, not a substitute for it.

The general form, and the reason it is worth the paragraph: **the bad news and the good news came
out of the same corrected predicate.** A 20% capture rate is a weak capability number and an
excellent cost-containment number, and v3 currently claims the strong version of the first and none
of the second.

---

## Is the core proposal still worth building? (the question neither attacker framed)

**Yes — build it. But the value case has to be restated on the addressable population, and the 20%
must be presented as both the coverage ceiling and the cost bound.**

Four reasons, in descending strength.

**1. The 80% has no location to preserve, so it is out of scope by construction rather than by
failure — and measuring against it is the same predicate error, run in the opposite direction.**
BT-7 exists for pain Б-8: *the location string rots with the first edit of the file.* A card that
never named a line has no rotting location and never had the pain. The addressable population is
exactly the 709 / 62 rows that name a place, and the design anchors **~95%** of them. That is a
strong result and it is the strongest defence of the feature — and neither attacker made it,
because both were auditing the number rather than the decision it feeds.

**2. The low capture rate is the design's own blast-radius control, and it is currently uncredited.**
Capture opens a file only after the grammar yields a place. Four adds in five never touch the
filesystem, never hold the lock across I/O, never reach the read-oracle surface and never store a
byte of `text`. Every risk in this review — SERIOUS-2's unbounded lock time, Codex F6's oracle,
SERIOUS-1's batch cost — is scoped by the same fraction that limits the benefit. A feature whose
cost and coverage are governed by one predicate is a *good* shape, and v3 argues neither half of it.

**3. The ceiling is measured on a corpus filed under the assumption that locations rot.** There is
no incentive to write a line number whose value decays on the next commit. If anchoring makes a
coordinate durable, filers acquire a reason to emit one — and structured `line=`/`end=` on `add`
(deferred in §4, recommended by the round-1 judge as C-A3) is the lever that moves the population
rather than merely measuring it. **State this as a hypothesis with a computable trigger, not as an
argument** — the document has already been burned three times for exactly that move.

**4. The tracker the owner is ratifying for is the one that scores better.** Codebugs sits at
34–47% against autosorter's 20%, and codebugs carries the AI-native filing discipline the design is
built around.

**What would change my answer.** If `text` must stay *and* the read cannot be narrowed to
source-controlled files, the feature buys ~20% coverage at the price of a general in-repository read
capability exposed on a public MCP verb — and at that price the trade is bad and the owner should be
told so plainly rather than sold the capability inside a location feature. That is precisely why
mandatory fix 3 is a question and not a fix.

**And the reframing the owner's question needs.** Not *"is 20% coverage enough to justify this?"*
but *"should the tracker read a file at add time for the one card in five that says where — knowing
that it solves ~95% of the cards that have the problem, and touches nothing on the other four?"*
Those are the same facts and different decisions, and only the second one is the decision the owner
is actually being asked to make.

---

## Design health score

**6 / 10 — substantial rework, not a rebuild.** I land on the same number as the Opus attacker and
partly on different reasoning, and the composite hides a split the holder needs to see: **as a
design I would score this 7–8; as a ratification instrument, 5.** Every load-bearing mechanism is
now either verified against code or measured against the corpus, and the two attackers between them
produced exactly one new *structural* hole (the capture root) and one undisclosed *capability* (the
in-repository read) — everything else is a number attached to the wrong claim, a contract left
unwritten, or a constant calibrated on the wrong corpus. Those are v4 edits, not a v4 rebuild.

**The direction survives a third round untouched, and the holder should be told so in as many
words.** Content anchor as core, resolve-on-read, anchor outside identity, storage in top-level
`meta.loc`, and capture inside the existing CB-45 resolver took **zero** attacks from either model
family in round 3 — after two rounds in which the storage model, the capture phase, the cascade and
the grammar were all overturned. §2.2's storage argument (the ring destroys its own repair path —
CB-26 reproduced inside CB-26's own fix), §4's dialect rule, the deletion of C2 on executed
evidence, and the §1а census should be carried into v4 **verbatim**. What is failing is not the
design's direction and not, any longer, its mechanisms; it is one seam — the one where a measured
number becomes a sentence addressed to the owner — and that seam now needs a mechanical gate rather
than a fourth attempt at care.
