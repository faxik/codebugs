# BT-7 v3 — hostile adversarial review (Opus, round 3)

Target: `.claude/plans/BT-7-location-anchor.md` (v3, commit `88ee00a`, 631 lines).
Read in full first: `review-x2-bt7-v2-opus-attack.md`, `review-x2-bt7-v2-codex-attack.md`,
`review-x2-bt7-judge-verdict.md`, `.claude/plans/BT-7-MEASUREMENTS-2026-08-22.md`.

**Everything numeric below was re-derived by me**, read-only, on 2026-08-22, from fresh exports
(`/tmp/bt7rev/cb.csv` 131 rows, `/tmp/bt7rev/as.csv` 3301 rows) and from the measurement doc's own
scripts, extracted verbatim out of §11 and re-run. Scripts and outputs live in `/tmp/bt7rev/`.

**Headline.** v3 is a real rebuild and the majority of round 2 is genuinely closed — the storage
re-decision, the collapse of two carriers into the existing CB-45 seam, the deletion of C2, the
worktree-root read binding, the persisted refusal object, the byte-cap fix, and the grammar
re-derivation are all closed by *mechanism*, and **every census number in §1а reproduces exactly**
on my own export. That is the best measurement work this design has carried.

But the document fails on the same axis it failed on twice: **the two numbers the owner is
actually asked to ratify against are computed on the wrong predicate.** §2.1 says option A "заякорить
может 2907/3300 и 125/129" and §8 q1 says the cost of "yes" is that "~12% карточек autosorter
заякорить нельзя вовсе". Measured: **20% of autosorter rows and 36% of codebugs rows are anchorable**
— because 79% and 53% respectively carry **no locational key at all**, a population §1а measures
(709 of 3301) and §4 never once addresses. The owner is being shown an 88% capability against a
measured 21% ceiling. That is "honoured in letter, falsified in fact", third round, in the ★ cell.

Second: the capture root. v3 fixed the *read* binding (Р5, worktree root via Т-0, `no_root`
fail-closed — a correct and complete answer to F-3). It then moved capture into the pre-add
resolver, where **there is no root channel at all**: `add_finding`/`batch_add_findings` have no
`project_dir`, and the observation literal (`findings.py:1058-1071`) carries no root. Capture can
only bind to the process cwd. F-3 was closed on one side and reopened on the other.

Third: the acceptance gate v3 introduces (§7.4, "рост не более 25%") is **failed by v3's own
pessimistic estimate at two of the four pool depths in its own table**.

---

## Closure audit

Legend: **CLOSED** = answered by a mechanism I could verify. **REWORDED** = answered by a sentence.
**OPEN** = not answered. **REPLACED** = the old defect is gone and a new one occupies its place.

### Round-2 Opus findings

| # | Round-2 finding | Verdict | Evidence |
|---|---|---|---|
| F-1 | Ring storage kills the repair path | **CLOSED** | §2.2 arg (2), lines 201-208, states the mechanism (`occurrences` ∈ `_RESERVED_META_KEYS`, `findings.py:235`; `ring[:10]+ring[-10:]`, `_OCC_KEEP_FIRST=10` verified by import) and uses it to *kill* (b). Storage is (a). Best single fix in the rebuild. |
| F-2 | Re-observation rate is zero | **CLOSED** | §1а line 119-122; §8 q2 carries the number. I reproduce **0/131 and 0/3301** at `occurrence_count>1`, **0 rings**, and 0 of 89 / 0 of 154 post-2026-08-16 rows. |
| F-3 | Read boundary anchored to `describe_root()` | **REPLACED** | Read side genuinely fixed (Р5 lines 316-325, `no_root` fail-closed). Write side: no root exists on the capture path at all — see **FATAL-1**. |
| S-1 | Two carriers, seventh registry, unpriced | **CLOSED** | П7 (lines 87-92) is correct: I read `findings.py:1058-1071`, the observation literal carries `file`, `description`, `meta`, `category`, `at`, `dedup_action`. One carrier, zero new seams. |
| S-2 | Р1 guarantee vs the new pre-lock seam | **CLOSED** | Capture is back inside the resolver. Р1's offsets reproduce exactly: `inspect.getsource(findings._add_one).find("_derive_fingerprint(")` = **3064**, `.find("run_pre_add_resolvers")` = **8031**. I also checked the flattening hazard independently: `_normalize_for_fingerprint` (`findings.py:430-457`) iterates top-level `meta.items()` and requires `isinstance(v, str)`, so a `loc` **dict** cannot shift `auto:v1`, and `normalize_categories`' re-derivation (`findings.py:2705`) therefore cannot mark anchored rows `unverifiable`. П9 holds. |
| S-3 | `sites`/`site` routed to a branch that cannot read them; basename gate unquantified | **CLOSED** | §4.1 B3 routes them; §4 publishes 22/23 and 41/47. Re-ran the doc's `measure3.py`: identical. |
| S-4 | C2's cost cell claims a parser that does not fit | **CLOSED** | §2.1 deletes C2 and cites the executed check (`_parse_rename_records` → `None` on `-U0`). |
| S-5 | `staleness_check` = two roots in one record | **CLOSED by removal** | Verified `staleness_check` (`provenance.py:801-806`) still has no `project_dir`. Р5's justification is exact. |
| S-6 | `get(resolve_anchor=)` needs core→extension | **CLOSED by removal — but see W-8** | Correct on the rule ("core must not know an extension's key names"). Cost is that the owner's *auto*-resolution is now further away, not closer. |
| S-7 | Three failure vocabularies | **CLOSED, not TOTAL** | §4.3 is one dictionary with a declared status/reason boundary — good. But it has **no token for "the capture code itself failed"** (see SERIOUS-4), and `unreachable_commit` is named in §5 and absent from the dictionary. |
| S-8 | Import strips the whole ring | **CLOSED** (moot under (a); Р2 names both asymmetries) |
| S-9 | Ring cap not rolling; anchor ring cost understated | **CLOSED** (used as the argument against (b)) |
| W-1 | `context_before/after` typed ambiguously | **CLOSED** — Р2 line 246: "ФАКТИЧЕСКИЕ ширины", ints |
| W-2 | Caps are names, not numbers | **CLOSED in form, attacked on derivation** — see FATAL-3 / SERIOUS-2 |
| W-3 | Third-digit disclaimer violated | **CLOSED** |
| W-4 | codebugs measurement undated | **CLOSED** (measurement doc, commit `c208c47`) |
| W-5 | Backfill has no commit to read from | **CLOSED**, except `unreachable_commit` is not in the §4.3 dictionary (S-7 residue) |
| W-6 | `anchor_recapture`'s write path | **OPEN** | v3 names the verb three times (Р3 line 296, Р4, §10.2) and never says whether it writes through `update_finding(meta_update=)` or issues its own UPDATE. That is the difference between `loc.py` staying zero-SQL (the `similarity.py` precedent) and it becoming the second module that writes another module's table. |
| W-7 | Deep copy per resolver carries the anchor | **CLOSED structurally** — the anchor never enters the observation; it is only the resolver's *return*. |
| W-8 | `describe_root()` nullability | **CLOSED** (`unknown(no_root)`) |
| W-9 | 2.4 s/100 extrapolation unmarked | **CLOSED** (replaced by a measured 4×2 table) |
| N-1 | Reason tokens carry payloads in provenance | **OPEN (cosmetic)** — v3's `out_of_repo` still collides by name with `provenance.py:345`'s `out_of_repo (worktree {root})`, which carries a payload. Two dictionaries, one token, different shapes. |
| N-2 | `loc` is free; the neighbourhood is crowded | **CLOSED** — П2 says exactly this. Reproduced: `loc` 0/131 and 0/3301; the crowd is real (`handler_loc`, `loc_src`, `loc_tests`, `fix_locus`, `proposed_loc`, `repo_loc`, `est_size_loc`, `root_cause_loc`, `ceiling_file_loc`, `name_gen_loc`, `baseline_files_loc`). `anchor` = **18** in autosorter (v3 corrected round-2's 16 — v3 is right). |
| N-6, N-7 | | **MOOT / CLOSED** |

### Round-2 Codex findings

| Codex finding | Verdict | Evidence |
|---|---|---|
| Final-symlink read admits `/etc/passwd` | **CLOSED** | §7.2, `realpath` of the full path + `S_ISREG` on the opened object, verified by execution. Residual below (SERIOUS-6): `realpath`-then-`open` is still TOCTOU; Codex asked for `O_NOFOLLOW`/fd-relative + `fstat`, v3 delivers the `fstat` half only. |
| No implementable pre-lock carrier | **CLOSED** (П7) |
| R1 invalidated by the new seam | **CLOSED** (offsets verified) |
| Capture failures unmeasurable | **CLOSED** — the persisted refusal object (Р2) is the right mechanism and is a genuine v3 invention |
| Tombstone/manual repair ineffective | **CLOSED** (storage (a)) |
| C2 has no mapping base / misses uncommitted edits | **CLOSED by deletion** — both reasons quoted in §2.1 |
| Default root reads main, not the linked worktree | **PARTIAL / REPLACED** — read fixed, write hole opened (FATAL-1) |
| Normalizer not reproducible | **CLOSED** in Р6 — with a caveat: the *calibration script* does not implement Р6 (NITPICK-2, measured null effect) |
| Manual updates persist malformed anchors | **DECLARED, NOT CLOSED** — legitimate as a named limit (§10.2), except for `path` (SERIOUS-3) |
| No sibling-key precedence | **CLOSED** (§4.2) |
| `sites_dropped` cannot see the rejected population | **CLOSED** (it lives in the refusal object too) |
| Promised surfaces absent from the cut | **CLOSED** (Т-b now carries the CLI verb) |
| Storage table biased / `locs:[…]` option missing | **DEFERRED with a trigger** — acceptable |
| `MAX_TEXT_BYTES` counted in codepoints | **CLOSED** (bytes, stated twice) |
| CB-65 "shared locus is a consumer" unsupported | **CLOSED** (§6 explicitly weakens it) |
| Measurements not reproducible | **CLOSED** — and this is the strongest thing in the round. I re-ran the embedded scripts and every §1а cell reproduces. |
| Batch benchmark without a gate | **CLOSED in form, self-contradicting** — SERIOUS-1 |
| *missing_requirements*: exact seam contract | **PARTIAL** — the seam is now the existing one, but the **path cache** it depends on has no specified scope or lifetime (FATAL-2) |
| *missing_requirements*: snapshot integrity | **REWORDED** — §7.6 says "при расхождении между двумя чтениями … `unknown`", and Р7 specifies exactly **one** read. The sentence describes a mechanism the algorithm does not contain. |
| *missing_requirements*: capture-commit invariant | **OPEN** — `captured_at_commit` is listed in Р2 and validated as "40 hex or null", and **nothing in v3 says what it means**: capture-time `HEAD`, the row's `reported_at_commit`, or `provenance._effective_commit`; nor what a dirty tree stores. With C2 gone the field has no reader at all. |
| *missing_requirements*: formal grammar | **PARTIAL** — regexes are published (in `measure3.py`), but sign/zero/descending-range/whitespace rules, nested-list recursion depth, and the exact `sites_dropped` counting rule for B5 are not stated |
| *missing_requirements*: complete MCP/CLI contract | **PARTIAL** — per-record shape is given; outer envelope, missing-id behaviour, filter defaults and vocabulary resolution, CLI output/exit codes are not |
| *missing_requirements*: backfill metrics | **PARTIAL** — and Т-c is still `опционально` (FATAL-2 in the mandatory table below) |

### Round-1 mandatory fixes, re-audited against v3

| # | Judge's mandatory fix | v3 |
|---|---|---|
| 1 | Storage: three options, one comparison, rename the key | **HONOURED** |
| 2 | Capture OUTSIDE the lock, two-phase, name+price the carrier | **DELIBERATELY REVERSED.** v3 puts capture *inside* `db.txn`. A reversal backed by measurement is legitimate under "preserve the intent, not the letter" — but the intent was *do not hold the write lock doing file I/O*, and the argument that discharges it uses the most flattering cell of its own table (SERIOUS-1) and violates its own acceptance gate (SERIOUS-1). Not carried. |
| 3 | Capture population = every observation; **if rejected, Т-c moves from optional to required** | **STILL OPEN — third consecutive round.** v3 rejects per-observation capture (§2.2a) and §9 keeps `Т-c — backfill dry-run через git show, **опционально**`. Round-2 Opus flagged the identical clause. |
| 4 | Containment = owner decision; `S_ISREG`; numeric caps; never raises; q4 | **MOSTLY HONOURED** — "never raises" is asserted, not mechanised (SERIOUS-4); the caps' *derivation* is attacked (FATAL-3) |
| 5 | Grammar by SHAPE sweep, `file:N` justified by the MATCHING count | **HONOURED** — reproduced exactly |
| 6 | Registration IS reservation | **HONOURED** |
| 7 | Owner questions non-contradictory, each with its cost | **HONOURED IN FORM, FALSIFIED IN FACT** — q1's cost line is off by 4× (FATAL-3… see FATAL-2 below) |
| 8 | ONE closed classification at all three sites | **HONOURED, NOT TOTAL** (SERIOUS-4) |
| 9 | Contract completeness / INVARIANTS | **HONOURED except `path`** (SERIOUS-3) |
| 10 | C1/C2 split, cascade | **RE-DECIDED with executed evidence — HONOURED** |
| 11 | Binding and caching for the read surface | **HONOURED on read; new hole on write** (FATAL-1) |
| 12 | §9 re-sliced | **HONOURED** |

**Tally: 8 honoured, 2 partial, 1 open for the third round, 1 deliberately reversed.**
The round-2 headline charge — *honoured in letter, falsified in fact* — **recurs**, in three places:
mandatory #3 (Т-c), q1's cost line (FATAL-2), and `probe_cost.py`'s final `print()` (FATAL-2's
companion, SERIOUS-1).

---

## FATAL

### FATAL-1. Capture has NO root channel — the fix for F-3 was applied to the read side and the write side has no coordinate system at all

v3 closes F-3 correctly and completely for resolution (Р5, lines 316-325): the root is the worktree
root via the Т-0 path API, never `describe_root()`, `unknown(no_root)` on failure. Verified: in a
linked worktree the two really do diverge, and `provenance._repo_root` is the right one.

Then Р3 (lines 285-291) puts capture inside the CB-45 resolver. I read the seam. **The resolver's
only inputs are `(conn, observation)`**, and the observation is a literal built at
`findings.py:1058-1071` whose complete key set is
`finding_id, severity, category, file, description, source, tags, meta, fingerprint, dedup_action,
recurrence_of, at`. **There is no root, and no channel that could carry one**: `add_finding`
(`findings.py:1200-1216`) and `batch_add_findings` (`:1800-1805`) take no `project_dir`/`repo`
argument, and П7 — the premise v3 calls "несущая" — is precisely the observation that the seam
already carries *everything capture needs*. It does not carry the one thing that decides which tree
is read.

So capture must resolve its root from the process cwd, i.e. `provenance._repo_root(_ambient_cwd())`.
Three consequences, all reachable today:

1. **A long-lived MCP server anchors against its own cwd, not the client's repo.** This is not
   hypothetical in this codebase — `provenance._ambient_cwd`'s own docstring (`provenance.py:16-30`)
   says *"a long-lived MCP server outlives the git worktree it was started in"*, which is why CB-79
   made it return `None`. A server started in `main` and used by an agent working in
   `.worktrees/fix-cb-nnn` will read `main`'s bytes, hash them, and store them as the branch's
   anchor. This is **verbatim the linked-worktree divergence v3 cites as the reason to reject
   `describe_root()`**, arriving through the door v3 did not close.
2. **The stored `path` and `hash` then disagree by construction**, and nothing detects it: the
   anchor resolves `current` against `main` and `lost` against the branch, or worse resolves
   `moved` to a line number that means nothing in the file the card is about.
3. **`--repo`/`--tracker-root`/`$CODEBUGS_ROOT` are exactly the population v3 invokes elsewhere**
   (П10's cross-repo `file` values), and they change the tracker root without changing the cwd.

v3 says nothing about this. §7.2 gives the containment *rule* and §7.3 gives the *policy question*;
neither names the root capture computes against. Р5's careful root paragraph is scoped to
`anchor_resolve` by its own first sentence ("Корень чтения…" inside Р5).

The precedent v3 leans on (П8, `reported_at_commit = git_rev_parse("HEAD")`) does **not** transfer:
a wrong commit id is a wrong label, a wrong root is *bytes from the wrong file persisted as this
card's location*.

**Fix required.** Either (a) capture resolves the root and **refuses unless it can prove the read
file is the one the `file` column names in the tracker's own worktree** — which needs a root that
`db` already knows and `findings` can pass, i.e. a real signature change after all; or (b) capture
stamps the root it used into the object (`loc.root`) and resolution refuses when it differs from the
resolution root — fail-closed on the unknown, per CLAUDE.md. (b) is cheap and is the honest minimum.
Either way, **the "ноль правок сигнатур ядра" headline of the rebuild does not survive contact with
the root problem**, and that should be said to the owner rather than discovered in Т-a.

### FATAL-2. The anchorable population is 20% / 36%, not 88% / 97% — the ★ option's capability cell and q1's cost line are both computed on the wrong predicate

§2.1, option A, "Цена" column (line 166): *"заякорить может **2907/3300 и 125/129**"*.
§8 q1, cost of "да" (line 552): *"**~12%** карточек autosorter заякорить нельзя вовсе"*.

Both numbers are the **`file`-column census** (§1а's last row: "колонка `file` — обычный читаемый
файл: 125 (97%) | 2907 (88%)"). Anchoring needs a *line*, and v3's own §1а says two rows earlier
that **only 61 codebugs and 709 autosorter rows carry any locational key at all**.

My re-derivation over the fresh exports, applying **v3's own §4 rules** (file always from the
column; a place is usable if the value yields a bare int, a `list[int]`, a bare spec, or a
`path:N` token whose basename equals the column's basename):

| | rows | **no locational key at all** | with a key | key + unreadable `file` | **ANCHORABLE** |
|---|---|---|---|---|---|
| codebugs | 131 | **69 (53%)** | 62 | 0 | **47 = 36%** |
| autosorter | 3301 | **2592 (79%)** | 709 | 32 | **656 = 20%** |

So the ratification question tells the owner that ~88% of his autosorter cards get an anchor and
~12% do not. The measured figure is the reverse: **~20% get one, ~80% do not.** The gap is not a
rounding disagreement, it is a different predicate — *the file exists* substituted for *the card
says where in it*.

§4 never addresses the no-key population. Every branch B1–B6 is a branch on the *value of a
locational key*; a row with no such key falls off the end of the grammar. The §4.3 vocabulary's
nearest token is `no_grammar`, which §4.1 assigns to prose. Under Р2, all 2592 autosorter rows get a
refusal object — which is the right *behaviour*, and is exactly why the number belongs in q1.

This is the round-1 and round-2 failure repeating: **the defect is in the section that claims to be
measured, and the measurement that contradicts it is two rows above it in the same table.**

**Fix required.** q1's cost line and §2.1's capability cell both become the anchorable count, with
the three-way split (no key / unreadable file / no usable place) shown, because the owner's decision
is *"is a 20% coverage feature worth file I/O under the write lock"*, and that is a materially
different question from the one currently on the page.

### FATAL-3. The calibration corpus that sets CONTEXT_LINES / MAX_ANCHOR_LINES / MIN_ANCHOR_CHARS is 94% vendored, CI and build trees — and the design doc does not say so

§1а and Р6 present the calibration as *"52 260 python-файлов обоих репозиториев"*. I re-ran the
doc's own `walk()` (`probe_calibrate2.py`, `SKIP = {.git, .venv, node_modules, __pycache__,
.worktrees, .codebugs, .mypy_cache}`) and printed the composition:

```
python files: 52260
top dirs: autosorter/actions-runner/_work  24056   (46%  GitHub-Actions runner work trees)
          autosorter/.venv312/lib          21369   (41%  vendored site-packages — .venv312 is
          autosorter/venv/lib               1497          NOT in the SKIP set, only `.venv` is)
          autosorter/build/lib              1279
          autosorter/git-split/venv          504
          autosorter/src/autosorter         1460   (2.8% first-party)
          autosorter/tests/*                 317
```

**First-party source is ~3 500 of 52 260 files — 6.3%.** The constants that decide how many bytes of
every card the tracker stores forever, and the "измеренный пол неоднозначности" that v3 promotes to
a first-class design fact (§10.1, Р7), are calibrated on vendored library code, a CI runner's
checkouts of other people's repositories, and duplicated build artifacts.

Re-run on **first-party files only** (same script, `SKIP` extended by `.venv312, venv,
actions-runner, build, git-split, dist, .tox, .eggs`), n = 3 551 files, 59 529 sampled positions:

| | doc (52 260 files) | **first-party (3 551 files)** |
|---|---|---|
| 1 line, non-unique in its own file | 25.8% | **24.10%** |
| 1 line, still non-unique after 3 lines of context | 3.82% | **1.17%** |
| 5 lines, non-unique | 6.6% | **2.95%** |
| 5 lines, still non-unique after 3 lines of context | 2.46% | **0.38%** |

The "measured floor of ambiguity 2.46–3.82%" that §10.1 elevates to a named limit is **3.3× to 6.5×
worse than the floor on the code this tracker is actually about.** The error is conservative, so it
does not endanger users — but it is the number the design uses to argue that `ambiguous` is a
first-class outcome, and it is wrong by a factor of six.

**The measurement doc discloses the vendored composition for the *size* census only** (§10 item 3,
"Файловая перепись включает вендорные и сгенерированные деревья… 58 914 файлов"), and justifies it
with *"трекер может указать на любой из них"*. That justification is measurable: **3 of 3301
autosorter findings point into a vendored/CI/build tree** (0.09%), and one of those is
`/home/faxik/w/git-split/git-split.py`, another repository entirely. §10 says **nothing** about the
composition of the **52 260-file uniqueness corpus** in §6.2, which is the one that sets the
constants. And **v3 itself carries no disclosure at all** — the owner reads v3, not the appendix.

Two further consequences of the wrong population:

- **`MAX_BYTES_READ = 1 MiB` is justified with "10 из 59 161 (0.02%)"** over the same vendored
  census, filtered to `.py .js .ts .md .sh .sql .yml .yaml .toml`. Measured over the population that
  actually matters — files a finding's `file` column names — **4 of 2917 autosorter references
  exceed 1 MiB (0.14%, 7× the quoted rate), and the largest is 25 MB**
  (`benchmarks/nightly/history.jsonl`; the others are `CHANGELOG.md`, 2.4 MB, referenced three
  times). Also **195 of 2917 referenced files (6.7%) have an extension outside the census entirely**
  — 154 of them `.svelte` — so for a fifth of the non-Python references the uniqueness statistics
  behind `CONTEXT_LINES` and `MIN_ANCHOR_CHARS` were never measured at all.
- **`CONTEXT_LINES = 3` does not follow from the data, and this is independent of the corpus
  complaint.** `probe_calibrate2.py` hardcodes `CTX = 3` and sweeps only `span ∈ (1, 2, 3, 5)`.
  **No alternative context width was ever measured**, so Р6's "измерено, что контекст снижает
  неоднозначность… в 6.8 раза" proves *that context helps*, never *that 3 is the number*. I swept
  it, span = 1, on **both** corpora — first on the document's own 52 260-file corpus (where my run
  reproduces its published figures to the digit: n = 1 014 546 sampled positions, 14.3% blank,
  25.88% non-unique, **ctx3 = 3.82%** — exactly §6.2's number), then on first-party source:

  | context | 1 | 2 | **3 (chosen)** | 5 |
  |---|---|---|---|---|
  | doc corpus, still non-unique | 10.21% | 5.76% | **3.82%** | **2.15%** |
  | first-party, still non-unique | 6.46% | 2.52% | **1.17%** | **0.35%** |

  There is no knee at 3 on either corpus; each step is worth ~1.8× (doc) to ~3× (first-party). And
  **ctx = 5 with a 1-line anchor (0.35% first-party, 2.15% doc) beats a 5-line anchor with ctx = 3
  (0.38% / 2.46%) while storing four fewer anchor lines in every card, forever.** The design tuned
  the expensive dial and froze the cheap one without comparing them — and Р6 draws the opposite
  conclusion ("длина не есть механизм уникальности"), when the data show both dials are worth ~3×
  each and multiply.

- **`MIN_ANCHOR_CHARS = 24` is a value inside a bucket, not a knee.** The published table has
  buckets 0-9 / 10-19 / 20-29 / 30-39 / 40-49 / 50-59 / 60+, and §6.2's own reading calls the knee
  "20–30 символов". Any value in 20…29 is equally supported by that resolution; 24 is placed near
  the data, not derived from it. Worse, **the table measures a SINGLE line** ("Неуникальность ОДНОЙ
  нормализованной строки по длине её тела") while Р6 applies the threshold to **"нормализованное
  тело всего якоря"** — up to five lines. The constant's unit is not the measurement's unit.

---

## SERIOUS

### SERIOUS-1. §7.4's own acceptance gate is failed by §7.5's own pessimistic estimate, at half the pool depths in §1а's own table

§7.4: *"гейт посадки — рост не более чем на 25% относительно той же глубины пула"*, against the
baseline 125 / 303 / 830 / 1337 ms for `batch_add_findings(100)` at pools 0 / 200 / 600 / 1200.
§7.5: *"Пессимистичная оценка (1 мс/файл на холодную, 100 разных путей) даёт +100 мс"*, and
concludes *"вывод переживает и пессимизм"*.

Arithmetic:

| pool | baseline | 25% gate | +100 ms is | verdict |
|---|---|---|---|---|
| ~0 | 125 ms | +31 ms | **+80%** | **FAILS** |
| ~200 | 303 ms | +76 ms | **+33%** | **FAILS** |
| ~600 | 830 ms | +208 ms | +12% | passes |
| ~1200 | 1337 ms | +334 ms | +7.5% | passes |

The conclusion "the argument survives pessimism" is true only for the two deepest pools. **A fresh
tracker — the state of every new project, and the state in which the feature will first be judged —
is pool ≈ 0**, where the design's own pessimistic number is 3.2× over its own landing gate. Т-a is
"обязан" to run exactly this comparison, so as written the unit is unlandable at small pools and
nobody has said so.

The same selection bias runs through the headline: **"захват стоит 2–8% от того, что write-lock уже
удерживает"** is the ratio of the two capture extremes (0.192 / 0.671 ms) to the **pool-600** add
cost (8.24 ms). Against the same table's other cells the ratio is **13–47% at pool ≈ 0** and
**5.5–19% at pool 200**. The number that killed the two-phase design is the most flattering cell of
a four-cell table, quoted without its range.

And the argument's *form* is one this repo has ratified against: CB-31's whole lesson is that
per-row work inside the `BEGIN IMMEDIATE` window is a defect *because every concurrent agent waits
behind it* — CLAUDE.md: *"`capacity._candidates` ran it per candidate row inside `pull_next`'s
`BEGIN IMMEDIATE` window, so every concurrent agent waited behind it."* "The lock already holds
16 ms" is not a licence, it is the *other* half of a defect. Used as v3 uses it, the argument proves
too much: it licenses any addition under 16 ms, forever.

### SERIOUS-2. File I/O under the write lock is bounded in BYTES and unbounded in TIME — and v3 names the class it fails to close

§7.1 is the whole guard: `S_ISREG` only, `MAX_BYTES_READ = 1 MiB`, NUL sniff, `errors="replace"`.
The stated reason for `S_ISREG` is exact: *"FIFO или устройство под локом заблокировали бы трекер
навсегда."* So v3 identifies the hazard class — **an unbounded block while holding
`BEGIN IMMEDIATE`** — and then closes exactly the two members it enumerated.

Members it does not close, all of which are regular files:

- **A file on a hung or slow NFS/SMB/FUSE mount.** `open()` and even the `S_ISREG` `stat()` block in
  uninterruptible sleep; a byte cap cannot fire on bytes that never arrive.
- **An autofs mount point** under the repo, where the first `stat` triggers a mount.
- **`realpath()` of the full path (§7.2)** stats every path component — more blocking syscalls
  before the byte cap is even reachable.
- A **symlink chain** inside the repo pointing at such a path; `realpath` follows it.

`db.connect()` sets `busy_timeout=5000` (CLAUDE.md, "Database"). A capture that blocks longer than
five seconds turns every concurrent `BEGIN IMMEDIATE` into `SQLITE_BUSY`, which is **not** in
`db._ENVIRONMENTAL_CODES` (`{8, 10, 13, 14}`, `db.py`) and is therefore not converted into
`TrackerUnwritableError`; it surfaces as a raw `sqlite3.OperationalError`. So a slow filesystem does
not degrade the anchor, it **converts other agents' adds into tracebacks.**

This is CLAUDE.md's most-repeated lesson landing on v3: *"a rule expressed as an enumeration gets
fixed at the sites someone enumerated, and the population is always larger than the list."* The
honest ograda is a **wall-clock budget** on the whole capture (per member and per batch), enforced
before the transaction opens where possible and abandoned into `unknown(timeout)` otherwise — a
token the §4.3 dictionary does not have.

### SERIOUS-3. The one field that decides which file gets opened is the one field with no invariant — and it is writable unvalidated

Р2's INVARIANTS block validates `v`, `line` (int, not bool, ≥1), `end`, the span cap, both hashes'
hex length, `text`'s byte length, and `captured_at_commit`. It says **nothing about `path`** — not
that it is relative, not that it is traversal-free, not that it is bounded, not that it is a string
at all.

`path` is the field that selects the file the resolver opens. And §10.2 concedes, correctly and by
execution (П12), that `update_finding(meta_update={"loc": …})` writes **whatever it is given**:
`_validate_meta_keys` (`findings.py:276-305`) checks *key names* only, and the merge is a bare
`new_meta.update(meta_update)` (`findings.py:2020-2021`). `restore_findings` bypasses validation
entirely. So `{"v":1,"path":"../../../etc/shadow","line":1,…}` is storable today by design, and the
read-side invariant list, which exists precisely because the write side does not validate, does not
look at it.

Containment probably saves it — `realpath(root + path)` escapes the root and §7.2 refuses. But
"probably" is doing the work: Р7 step 1 says only *"прочитать файл (ограничения §7.1)"*, and §7.1
is the byte/kind cap while §7.2 is written as a **capture-time** boundary ("для ЧТЕНИЯ И СОХРАНЕНИЯ
БАЙТОВ"). Whether the *resolution* path re-applies containment to a stored, attacker-writable `path`
is left to the reader.

This is also CLAUDE.md's composition rule in miniature: *"a check that validates elements cannot
validate their composition."* v3's invariants validate seven elements individually and validate
**no** relation between them — nothing checks that `hash` was computed over `text`, that
`end - line + 1` equals the line count of `text`, or that `context_before/after` are consistent with
the widths that produced `context_hash`. A `loc` object with every field individually legal and
jointly meaningless passes every invariant and produces a confident `current`.

### SERIOUS-4. "Capture failure is never `resolver_errors`" is a promise the seam cannot keep, and the closed vocabulary has no token for the case where it is broken

§4.3: *"Отказ захвата НИКОГДА не исключение и никогда не `resolver_errors`."* I read the runner
(`db.run_pre_add_resolvers`, `db.py:379-464`). It wraps every resolver call in
`except Exception: … errors.append(...)` and stamps `patch["resolver_errors"]`. So the promise holds
**only as a property of `loc.py`'s own code** — it must catch every exception internally and return
a refusal object. That is achievable, and it is what `similarity` does, so the design is
*reconcilable*. Two things follow that v3 does not say:

1. **It is a sentence, not a mechanism.** §9's Т-a lists tests for the behaviour shift
   (`add`/`update` refusing `meta.loc`, import stripping) and **no test pinning that capture never
   raises**. Mandatory fix #4 says "capture never raises"; v3 honours it by assertion. A single
   `except BaseException`-shaped test on a resolver that is fed a hostile path is the mechanism.
2. **The vocabulary is not TOTAL over the outcomes.** The 13 reason tokens cover environmental and
   grammatical refusals. There is **no token for "the capture code itself failed"**. So a bug in
   `loc.py` has exactly two possible fates: it escapes and becomes `resolver_errors` (contradicting
   §4.3's promise, and leaving **no `loc` key at all** — which reintroduces precisely the
   "`null` неотличим от «строка заведена до якоря»" ambiguity Р2's refusal object was invented to
   kill), or it is mapped onto one of the 13 tokens and lies about why. This repo pins branch
   totality for exactly this reason (`tests/test_dedup.py::TestBranchTotality`) — CLAUDE.md:
   *"an unclassified status silently resumes the duplicate explosion"*. Add `internal_error` and
   fail closed onto it.

### SERIOUS-5. The batch path cache — on which the entire batch-cost argument rests — has no specified scope, lifetime or invalidation, and the seam gives it no batch handle

Р3: *"захват кеширует ПО ПУТИ, так что цена батча растёт по числу РАЗНЫХ путей, а не членов"*, and
§7.4 repeats it. The resolver's signature is `fn(conn, observation)` and it is invoked **once per
member** (`_add_one` → `run_pre_add_resolvers`, per member, inside one transaction). **There is no
batch identity anywhere in the seam.** A per-call cache caches nothing; therefore the cache must be
**module-global mutable state in `loc.py`**, and v3 never says so, never bounds it, and never says
when it is invalidated.

Both failure modes are real:

- **Never invalidated** → a long-lived MCP server captures anchors from bytes it read hours ago. The
  anchor's `hash` then describes a version of the file that no longer exists, so the very first
  `anchor_resolve` returns `lost`. A stale-cache anchor is *worse* than no anchor: it is a confident
  wrong record.
- **Unbounded** → a 3000-member import-shaped batch caches up to 3000 files' contents inside the
  write lock. No memory bound is stated anywhere.

And the empirical premise is weak. I measured the distinct-path ratio of real filing groups (rows
sharing a `created_at` second, ≥10 rows, both corpora):

```
(rows, distinct paths): (74,8) (37,28) (31,29) (27,6) (24,4) (23,14) (21,12) (21,2) (20,20) (20,19) (20,17) (20,14)
aggregate: 745 rows -> 416 distinct paths = 56% distinct
```

So the cache saves ~44% on average and **~5% on the two largest diverse batches** (37→28, 31→29).
Worse, `probe_cost.py`'s last two lines are:

```python
print("\nA batch touches few distinct paths — measured over the live corpora in pass 1:")
print("  (capture caches per distinct path, so batch cost ~= distinct paths, not members)")
```

That is a **hardcoded `print` of a conclusion**, in the script the document offers as its proof, and
it is contradicted by the corpora it claims to cite. This is the single clearest instance of the
round-2 charge recurring: a measurement artifact that *prints* the finding instead of computing it.

### SERIOUS-6. §7.2 closes the symlink hole with `realpath` and leaves the race Codex asked to close

§7.2's finding is real and well-earned (I accept the executed evidence). But `realpath(path)` →
`commonpath` check → `open()` → `fstat` is a **check-then-open** sequence: the path can be replaced
between `realpath` and `open`. Codex's `missing_requirements` asked for `O_NOFOLLOW` or an
fd-relative open plus `fstat`, *and* race handling. v3 delivers the `fstat`-on-the-opened-object half
and states the other half nowhere. Low practical severity (the adversary must be able to write into
the repo), but it is a security boundary the owner is being asked to ratify, and half a boundary
should be declared as half.

### SERIOUS-7. `text` is stored, capped, argued about — and never read by any algorithm in the document

Р2 stores `text` (≤ 2048 bytes). Р7's resolution algorithm uses `hash` and `context_hash` and
**never touches `text`**. No section names a consumer. It is simultaneously:

- the largest field in the object, hence the whole of §2.2's "стоимость места" row — the argument v3
  uses *against* storage (b);
- the only field that copies **source-code bytes into the tracker database and into every
  `export-csv`** (import strips `loc`; the CSV on disk does not);
- and the reason §7.3's owner question about out-of-repo reads matters at all.

If `text` has a purpose (human display in `anchor_resolve`? re-deriving a hash after a normalizer
version bump?), say it and price it. If it does not, deleting it removes 2048 bytes per card, most
of the byte-cost argument, and most of the content-exfiltration surface in one line. As written, the
design asks the owner to ratify persisting file contents for a field with no stated reader.

### SERIOUS-8. The grammar's load-bearing rule is measured on autosorter and is the MINORITY dialect in codebugs

§4's rule — *"Файл — это ВСЕГДА колонка `file`"* — is introduced with:
*"609 из 709 строк autosorter (86%) и 23 из 61 в codebugs, несущих локационный ключ, не называют в
значении ни одного имени файла"*, presented as one supporting fact in two corpora.

Reproduced (my export, +1 row of drift): autosorter **609/709 = 86%**; codebugs **24/62 = 39%**.
In codebugs, **61% of locational rows DO name a filename** — the "dominant dialect" is dominant in
one corpus and is the minority in the other, and the document reads the two numbers as agreeing.
The sentence is literally true and the inference from it is corpus-specific.

The rule is still probably right — see the credit in "Measurement reproduction" below, where all
seven disagreement rows favour the column — but the *justification presented to the owner* is
weaker than it reads, and the tracker whose owner is ratifying is the one that disagrees.

### SERIOUS-9. Basename equality is not corroboration, and the corpus contains the counterexample

§4: *"Имя файла внутри значения не выбирает файл; оно либо подтверждает колонку, либо дисквалифицирует
свои номера строк"*, implemented in B3 as *"чьё базовое имя равно базовому имени колонки `file`"*.

Basename equality across different directories is common in Python trees (`__init__.py`, `utils.py`,
`models.py`, `entities.py`). Measured — rows where a token's basename equals the column's basename
but the token carries a **different** directory path (not a suffix of the column):

```
autosorter, 2 rows:
  CB-2959  column src/autosorter/core/db/entity_corrections.py  token core/entity_corrections.py
  CB-2944  column src/autosorter/core/entities.py              token cli/commands/entities.py
codebugs: 0 rows
```

CB-2944 is the failure the rule denies is possible: `cli/commands/entities.py` and
`core/entities.py` are **different files**, the gate passes on the basename, and the token's line
numbers are adopted as coordinates in the wrong file — a *confidently wrong* anchor, which Р7 is at
pains to avoid for `ambiguous` and does not avoid here. Two rows is small; the rule is stated
without qualification and the counterexample exists.

Cheap fix, no new mechanism: when the token carries a directory component, require the column path
to **end with** the token (suffix match) rather than merely share a basename; a bare basename token
keeps today's behaviour. I measured the cost: **18 of 41 autosorter gate-passing rows carry a
directory**, of which the suffix rule keeps 16 and refuses the 2 above.

---

## WEAKNESS

- **W-1. §4.1's branch table does not partition its own population.** Reproducing `measure3.py`:
  autosorter `line` splits **95 B1 + 3 B4**, and v3's B1 row reads "`line` **95+3**" — the `+N`
  convention everywhere else in that table means *codebugs*, and codebugs has **zero** `line` rows.
  The 3 bare specs are B4, not B1. Also missing from the table entirely: codebugs `sites` B3 = **6**;
  autosorter `lines` B6-with-digits = **7**; `site` B6 = **1**; codebugs `lines` B6 = **2**. And the
  2 dict-valued `sites` rows are counted **twice** — once in B3 ("dict значений (2)") and once in
  B5 ("`sites` 2"). A grammar census that does not partition is the one artifact in §4 that must.
- **W-2. §4.2's priority order is justified with a ratio over the wrong denominator.** *"`site` 0%
  многофайловых и 15/15 прохождений гейта"* — §1а says `site` = 16 rows; the 16th is prose. "15/15"
  is over the 15 rows that carry tokens, which reads as a 100% property of the key. Same shape as
  the "2/31" denominator round 2 flagged.
- **W-3. `MAX_TEXT_BYTES = 2048` is introduced as parity with `_OCC_DESC_CAP = 2000`** *"чтобы в одном
  пакете не появилось двух несогласованных представлений о «разумном куске текста»"* — and then
  chooses a different number. Verified: `findings._OCC_DESC_CAP == 2000`. The clause creates the
  inconsistency its own sentence forbids. Either 2000 or a stated reason for 2048.
- **W-4. Р7 reads the file once; §7.6 promises a two-read divergence check.** *"при расхождении между
  двумя чтениями честный ответ — `unknown`"* describes a mechanism the algorithm does not contain,
  and `resolved_against` (`head`, `mtime_ns`, `size`) is explicitly *"свидетельство, а не
  доказательство"*. Either the algorithm re-stats after reading and compares, or §7.6 should say
  plainly that TOCTOU is accepted unmitigated.
- **W-5. `captured_at_commit` has no definition and, since C2's deletion, no reader.** Р2 lists it and
  validates it; nothing says whether it is capture-time `HEAD`, the row's `reported_at_commit`, or
  `provenance._effective_commit`, nor what a dirty tree stores. Codex asked for exactly this in
  round 2 and it is untouched.
- **W-6. Resolution cost is never measured while capture cost is measured to three digits.**
  `anchor_resolve` over a filter must read every referenced file and hash **every window** in it
  (Р7: *"Радиуса поиска нет… сканируется весь файл"*), plus a `git rev-parse` per record for
  `resolved_against.head`. At p99 = 3 369 lines that is thousands of sha256 per file, and for
  duplicates a second context hash per candidate. §5 defers the cache *"по замеренной цене"* — of a
  cost nobody has measured. The design measured the cheap side.
- **W-7. `anchor_resolve`'s filters inherit two of this repo's ratified traps and mention neither.**
  `status`/`category` are vocabulary filters (CB-19: *"a vocabulary must resolve on BOTH sides"*;
  CB-25: *"'no filter' is `None` and `''` — never truthiness"*), and the `check_findings` precedent
  it copies maps `None`/`""` to `"open"` rather than to "no filter". v3 specifies neither the
  resolution nor the default.
- **W-8. The owner asked for *auto*-resolution and v3 moved further from it than v2.** CB-95 verbatim:
  *"Ideally, with auto-resolution of the new line."* v2 offered `get(resolve_anchor=True)` and
  `staleness_check(anchor=True)`; round-2 W-8 said an opt-in flag is a partial answer. v3's response
  was to delete both, leaving resolution reachable only through a verb the caller must know exists.
  The architectural reasons are correct and I would keep the decision — but it is a **narrowing of
  the owner's request**, and under his own standing rule ("divergence in meaning, not spelling")
  that belongs in a question, not in a §10 bullet. Right now §10.5 states the mechanism and never
  says the request was narrowed.
- **W-9. The `sites_dropped` trigger is a count without identity.** §5 makes "распределение
  `sites_dropped`" the trigger for the multi-anchor table, but the counter records *how many* places
  were dropped and never *which*, so the decision it gates is taken on a histogram. Storing the
  dropped specs (bounded) or nothing at all would both be more honest than a number that cannot
  answer the question it is the trigger for.
- **W-10. The capture-cost probe does not implement the capture that is specified.** `probe_cost.py`'s
  `capture_once` is `open` + `read` + `strip()` + two `sha256`. It contains no `realpath`, no
  `lstat`/`S_ISREG`, no NUL sniff, no tab→space, no indent-depth token, no whitespace collapse, no
  `MIN_ANCHOR_CHARS`, no grammar parse, and no `os.stat` for `mtime_ns`/`size`. Every omitted item
  is either a syscall or a per-line pass, and all of them run under the lock. 0.192 ms/file is a
  floor for a simpler function than the one Р6 specifies, measured over **28 files of
  `src/codebugs/` only**, warm, one run.

---

## NITPICK

- **N-1. Р1's proof is cited in character offsets into a function's source text.** *"`_derive_fingerprint`
  вызывается на смещении 3064, `run_pre_add_resolvers` — на 8031"*. It reproduces exactly
  (`inspect.getsource(findings._add_one).find(...)` → 3064 / 8031, len 10218), so the claim is true
  and checkable — but it is checkable only by running Python, and it goes stale on the next edit to
  `_add_one`, whereas `findings.py:946` / `:1058` are greppable and survive. An odd coordinate
  system for the document's central guarantee.
- **N-2. The calibration script's own normalizer is not Р6's.** Measurements doc line 1642:
  `re.sub(r'\\s+', ' ', s.strip())` — a raw string, so the pattern is *literal backslash followed by
  one or more `s`*, and **internal whitespace is never collapsed**. Р6 mandates collapsing. I
  measured the consequence and it is **nil** on this corpus (identical figures under both
  normalizers, because `strip()` plus PEP8 leaves almost no internal whitespace runs in Python
  source) — so this is not a numbers finding. It is a finding about the artifact: the document's
  claim is that the scripts are published *"чтобы любой рецензент мог перевыполнить замер"*, and the
  published script does not implement the spec it calibrates. Direction of error, had it bitten:
  collapsing merges more lines, so the true non-uniqueness would be **higher** than published.
- **N-3. `out_of_repo` collides by name with `provenance.py:345`'s `out_of_repo (worktree {root})`**,
  which carries a payload v3's bare token does not. Round-2 N-1, still open, still cosmetic.
- **N-4. `unreachable_commit` (§5, backfill) is not in §4.3's closed dictionary.** Round-2 W-5's
  residue.
- **N-5. §7.1's two limits are not jointly satisfiable as stated.** *"строк p99 = 3 369, max =
  135 926"* is offered next to `MAX_BYTES_READ = 1 MiB`; a 135 926-line file averaging more than
  8 bytes per line is already `too_large`, so the `out_of_range` cap is being justified by a file
  the byte cap refuses first. Harmless, but it is a number placed near an argument rather than
  supporting it.
- **N-6. §1а's cost table is one run per cell** (§10.5 discloses it; v3 does not) and the table's
  four values are quoted to three significant figures (`16.23 ms`, `1337 ms`) in the document that
  elsewhere insists on publishing "порядок, не третью цифру".

---

## Measurement reproduction

Exports taken by me today: `uv run codebugs export-csv /tmp/bt7rev/cb.csv` (131 rows),
`uv run codebugs --tracker-root /home/faxik/w/autosorter export-csv /tmp/bt7rev/as.csv` (3301 rows).
v3's snapshot was 129/3300, so +2/+1 rows of drift is expected and is visible below.

| claim (v3) | claimed | my value | verdict |
|---|---|---|---|
| re-observations, `occurrence_count>1` | 0 / 0 | **0/131, 0/3301** | **REPRODUCES** |
| rows carrying an occurrence ring | 0 / 0 | **0 / 0** | **REPRODUCES** |
| filed after CB-43, of which re-observed | 87→0 / 153→0 | **89→0 / 154→0** | **REPRODUCES** (drift) |
| rows with any locational key | 61 / 709 | **62 / 709** | **REPRODUCES** (drift) |
| of those, naming no filename | 23 / 609 (86%) | **24 / 609** | **REPRODUCES** — but see SERIOUS-8 for what 24/62 means |
| `lines/line/sites/site` | 44/0/18/0, 559/98/36/16 | **44/0/19/0, 559/98/36/16** | **REPRODUCES** (drift) |
| `function` 28, `location` 1, `anchor` 18, `loc` 0 | — | **28, 1, 18, 0/0** | **REPRODUCES** |
| multi-PLACE for `lines` | 24 (54%) / 268 (47%) | **24 (54%) / 268 (47%)** | **REPRODUCES** |
| multi-FILE for `lines` | 12 (27%) / 30 (5%) | **12 (27%) / 30 (5%)** | **REPRODUCES** |
| rows with ≥2 locational keys | 1 / 28 | **1 / 28** | **REPRODUCES**, with the same breakdown |
| basename gate: single-name agreement | 22/23 / 41/47 | **22/23 / 41/47** | **REPRODUCES** |
| `site` unambiguous | 15/15, 0% multi-file | **15/15, 0%** | **REPRODUCES** |
| `sites` 63% multi-file | 63% | **63%** | **REPRODUCES** |
| `file` column is a readable regular file | 125 (97%) / 2907 (88%) | **127 / 2908** | **REPRODUCES** (drift) |
| non-file `file` values in autosorter | 197+167+22+7 = 393 | **197+167+22+7 = 393** | **REPRODUCES** |
| Р1 offsets 3064 / 8031 | — | **3064 / 8031** | **REPRODUCES** |
| `_OCC_DESC_CAP`, `_OCC_KEEP_FIRST/LAST` | 2000, 10/10 | **2000, 10/10** | **REPRODUCES** |
| **"заякорить может 2907/3300 и 125/129"** | 88% / 97% | **656/3301 = 20%, 47/131 = 36%** | **REFUTED — FATAL-2** |
| **"~12% карточек нельзя заякорить"** | 12% | **~80% (autosorter), ~64% (codebugs)** | **REFUTED — FATAL-2** |
| **calibration corpus = "52 260 python-файлов обоих репозиториев"** | first-party source | **6.3% first-party; 46% `actions-runner/_work`, 41% `.venv312`/`venv`/`git-split/venv`, 2.4% `build/lib`** | **REFUTED as characterised — FATAL-3** |
| **ambiguity floor 2.46–3.82%** | measured floor | **first-party: 0.38–1.17%** | **6.5× / 3.3× overstated — FATAL-3** |
| §6.2 uniqueness table (25.8% / 3.82%) on the doc's own corpus | 25.8%, 3.82%, n≈1.01M, blank 14.3% | **25.88%, 3.82%, n = 1 014 546, blank 14.3%** | **REPRODUCES to the digit — credit** |
| **`CONTEXT_LINES = 3` "измерено"** | derived | **`CTX = 3` is hardcoded in `probe_calibrate2.py`; no width was ever swept.** My sweep, span 1 — doc corpus: ctx1 10.21%, ctx2 5.76%, ctx3 3.82%, **ctx5 2.15%**; first-party: 6.46 / 2.52 / 1.17 / **0.35%** | **NOT DERIVED — FATAL-3** |
| **files >1 MiB "10 из 59 161 (0.02%)"** | 0.02% | over the population that matters (`file`-column referents): **4/2917 = 0.14%, max 25 MB** | **wrong population — FATAL-3** |
| `MIN_ANCHOR_CHARS = 24` | knee at 20–30 | buckets are 10 wide; 24 is inside a bucket, and the table measures **one line** while the constant caps **the whole anchor** | **placed, not derived** |
| **"батч трогает мало разных путей"** | asserted by a `print()` in `probe_cost.py` | **56% distinct over all ≥10-row filing groups; 28/37 and 29/31 on the two largest diverse ones** | **REFUTED — SERIOUS-5** |
| "захват стоит 2–8%" | 2–8% | 2.3–8.1% **only at pool 600**; **13–47% at pool 0**, 5.5–19% at pool 200 | **cherry-picked cell — SERIOUS-1** |
| §7.4 gate (+25%) vs §7.5 pessimism (+100 ms) | "вывод переживает пессимизм" | **fails the gate at pool 0 (+80%) and pool 200 (+33%)** | **self-contradicting — SERIOUS-1** |
| §4.1 branch table | partitions the population | `line` 95 B1 **+ 3 B4** (labelled `95+3` in the B1 row); codebugs `sites` B3=6 missing; dict-`sites` counted in both B3 and B5 | **does not partition — W-1** |
| П9 flattening trap (nested dict is safe) | safe | verified: `_normalize_for_fingerprint` only strips **top-level `str`** values, so a `loc` dict cannot move `auto:v1` **and cannot make `normalize_categories` report `unverifiable`** | **REPRODUCES, and the CB-61 interaction is clean** |
| П7 (observation already carries what capture needs) | — | verified at `findings.py:1058-1071` | **REPRODUCES — and see FATAL-1 for what it does NOT carry** |
| П12 (`updatable_keys` validates nothing) | — | verified: `_validate_meta_keys` checks names; `new_meta.update(meta_update)` at `findings.py:2020` | **REPRODUCES** |
| the 7 single-filename disagreements | "token disqualifies" | **all 6 autosorter tokens name files that do not exist** (`dispatch_policy.py`, `steps_knowledge.py`, `entity_resolution.py`, `page.svelte`, `server.py`, `planner.py`) while every column does; codebugs' one (CB-70) names a real but unrelated file | **v3's rule is right on 7/7 — credit** |
| "column is prose/glob but a token names a real file" (a class v3's rule would lose) | not measured | **0 rows in either corpus** | **v3's rule loses nothing here — credit** |

---

## Summary scorecard

| Axis | v1 | v2 | **v3** | Note |
|---|---|---|---|---|
| Premises verified against code | 3 | 8 | **9** | П6/П7/П9/П11/П12 all re-verified by me; П7 is a genuinely load-bearing discovery |
| Corpus census fidelity | 2 | 8 | **10** | every §1а cell reproduces on a fresh export |
| Calibration fidelity | — | 2 | **4** | the published figures reproduce to the digit — but on a 94% vendored corpus, with an unswept constant, a wrong-population cap, and a script that does not implement the spec it calibrates |
| Grammar correctness | 2 | 6 | **8** | the new rule is right where I could test it (7/7 disagreements, 0 lost rows); the branch table does not partition and basename ≠ corroboration |
| Storage decision | 2 | 3 | **9** | (a) with two independent arguments, the second of which is code-level and decisive |
| Capture mechanism | 1 | 6 | **5** | zero new seams is excellent; the lock argument is cherry-picked, the cache is unspecified, and there is no root |
| Read boundary / security | 2 | 4 | **7** | worktree root + `no_root` + full-path `realpath` are right; `path` has no invariant and the open is still check-then-open |
| Contract completeness | 3 | 8 | **8** | INVARIANTS, statuses, one vocabulary — minus `path`, minus totality, minus `captured_at_commit`'s meaning |
| Owner questions | 1 | 7 | **5** | form is exemplary; q1's cost line is off by 4× and q2 is genuinely dissolved by measurement |
| Internal consistency | 3 | 5 | **6** | gate vs pessimism, §7.6 vs Р7, 2048 vs 2000, B1's `95+3` |

**Overall: 6 / 10 — substantial rework, not a rebuild.**

The direction survives a third round untouched: content anchor as core, resolve-on-read, anchor
outside identity, storage in top-level `meta.loc`, capture in the existing CB-45 resolver. Nothing
here attacks any of those, and §2.2, §4's rule and the deletion of C2 should be kept verbatim. The
work that produced §1а is the best in three rounds and should be said so to the owner.

**What must change before this reaches the owner, in order:**

1. **Fix q1's cost line and §2.1's capability cell to the anchorable population** — 20% / 36%, with
   the three-way split. The owner is currently deciding on an 88% promise against a 21% ceiling, and
   the number that refutes it is two rows above it in v3's own table. (FATAL-2)
2. **Name the capture root, or admit the signature change.** Capture has no root channel; the seam
   cannot carry one; the process cwd is not the worktree. At minimum stamp `loc.root` and refuse on
   divergence at resolution, fail-closed. (FATAL-1)
3. **Re-derive the constants on first-party source, and sweep `CONTEXT_LINES`.** State the corpus
   composition in v3 itself, not only in the appendix. On the swept data, `CONTEXT_LINES = 5` with
   `MAX_ANCHOR_LINES = 1–3` dominates the current pair on both ambiguity and stored bytes.
   Re-derive `MAX_BYTES_READ` on the `file`-column population, where 25 MB references exist.
   (FATAL-3)
4. **Resolve the lock argument honestly**: publish the 13–47% ratio at pool ≈ 0 beside the 2–8%, and
   either raise the §7.4 gate or acknowledge that §7.5's own pessimistic estimate fails it at two of
   four pool depths. Add a **wall-clock** budget for capture, with an `unknown(timeout)` token —
   `S_ISREG` closes two members of a class whose remaining members are all regular files.
   (SERIOUS-1, SERIOUS-2)
5. **Specify the path cache** — scope, lifetime, invalidation key, memory bound — and delete the
   `print()` in `probe_cost.py` that asserts its benefit; the measured distinct-path ratio is 56%.
   (SERIOUS-5)
6. **Give `path` an invariant** (relative, traversal-free, bounded, string) and add `internal_error`
   to the vocabulary so it is total; add the "capture never raises" test to Т-a. (SERIOUS-3,
   SERIOUS-4)
7. **Make Т-c required, or say in §5 that the triggers are computable over new inserts only** — this
   is the judge's mandatory fix #3, open for the third consecutive round.
8. **Either justify `text` with a reader or delete it.** (SERIOUS-7)
9. **Make §4.1 partition its population**, and replace basename equality with a suffix match when
   the token carries a directory (cost measured: 2 rows refused, 16 kept). (W-1, SERIOUS-9)
