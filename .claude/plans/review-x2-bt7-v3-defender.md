# BT-7 v3 — defender ruling over the union of the round-3 findings

Target: `.claude/plans/BT-7-location-anchor.md` (v3, commit `88ee00a`).
Measurement record: `.claude/plans/BT-7-MEASUREMENTS-2026-08-22.md` (commit `c208c47`).
Attackers: `review-x2-bt7-v3-opus-attack.md` (Opus), `review-x2-bt7-v3-codex-attack.md` (Codex/Sol).
Defender: Opus, read-only, 2026-08-22. No tracker write verb was issued; both corpora were taken
with `codebugs export-csv` into `/tmp/bt7def/` (**codebugs 138 rows, autosorter 3303 rows** — the
live counts today; v3 measured 129/3300 and the two attackers 131/3301, so every row below carries
two days of drift).

**Everything I ruled on, I re-derived myself.** In particular I re-ran: the anchorable population
under §4's grammar; the calibration corpus composition; a full `CONTEXT_LINES` sweep on **both** the
document's own 52k-file corpus and a first-party-only corpus; the referenced-file size population;
the filing-group distinct-path ratio; the branch census; the basename-gate counterexamples; and the
warm cost of `git rev-parse --show-toplevel`. Scripts and outputs are in `/tmp/bt7def/`.

**Tally: 53 CONCEDE / 13 PARTIAL / 2 DEFEND** across 68 ruled rows (36 Opus + 32 Codex). About
thirteen rows are the same finding reported by both families; each keeps its row and carries a
cross-reference, so the per-family record stays readable.

The two DEFENDs, stated up front because they are the two places the fix list would do damage if it
merged the findings uncritically:

- **Opus W-1's sub-claim that the 3 non-int `line` values are B4 is factually wrong.** Measured:
  they are the int-*strings* `"51"` (CB-1886), `"13"` (CB-1875), `"45"` (CB-1873), and B1's own
  definition is *"голый `int` или int-строка"*. The `95+3` cell is a type split **inside B1**, not a
  B1/B4 split. The rest of W-1 stands (see the row).
- **Judge's mandatory fix #2 ("capture OUTSIDE the lock") was legitimately reversed, and the fix
  list must not re-reverse it.** The arithmetic v3 uses to justify the reversal is cherry-picked
  (SERIOUS-1a, conceded) and the *time* bound is missing (SERIOUS-2, conceded) — but the placement
  itself sits on a ratified precedent: `similarity._annotate_resolver` already runs **per member,
  inside the same `BEGIN IMMEDIATE`**, and is what the 8.24/16.23 ms add cost is *made of*. Adding
  0.19–0.67 ms of CPU to that is not the CB-31 defect; **unbounded blocking time** is, and that is a
  different fix (a wall-clock budget) from moving the capture out.

---

## A. Opus findings

| # | ruling | evidence |
|---|---|---|
| **FATAL-1** capture has no root channel | **CONCEDE** | `add_finding` (`findings.py:1200-1216`) and `batch_add_findings` (`:1800-1805`) take no `project_dir`/`repo`; the observation literal (`findings.py:1058-1071`) is exactly `finding_id, severity, category, file, description, source, tags, meta, fingerprint, dedup_action, recurrence_of, at`; `db.run_pre_add_resolvers(conn, observation, *, forbidden)` (`db.py:379-384`) passes nothing else. There is no root and no channel that could carry one. П7 — "the seam already carries everything capture needs" — is true of every input **except the one that decides which tree is read**. |
| **FATAL-2** anchorable population is 20%/36%, not 88%/97% | **CONCEDE** (holder already confirmed) | My independent re-derivation under §4's own rules: **autosorter 656/3303 = 19.9%**, **codebugs 47/138 = 34.1%** — identical to Opus's 656/47 on a corpus three rows larger. See "the three numbers" below for why the holder got 675 and Codex 677. |
| **FATAL-3a** calibration corpus is ~94% vendored/CI/build | **CONCEDE** | Re-ran the doc's own `walk()`: **52,261 python files**, of which `autosorter/actions-runner/_work` **24,056 (46.0%)**, `.venv312/lib` **21,369 (40.9%)**, `venv/lib` 1,497 (2.9%), `build/lib` 1,279 (2.4%), `git-split/venv` 504 (1.0%) — **first-party 3,552 = 6.8%**. The cause is in the script: `SKIP` (`MEASUREMENTS:1618`) holds `.venv` and not `.venv312`, `venv`, `actions-runner`, `build`. §10 item 3 discloses vendoring **for the size census only** and says nothing about the 52k uniqueness corpus that sets the constants; v3 itself discloses neither. |
| **FATAL-3b** `CONTEXT_LINES = 3` was never swept | **CONCEDE** | `CTX = 3` is a hardcoded module constant (`MEASUREMENTS:1684`); the loop sweeps `span ∈ (1,2,3,5)` only. Р6's *"измерено, что контекст снижает неоднозначность в 6.8 раза"* proves that context helps, never that 3 is the value. |
| **FATAL-3b′** therefore `(span 1, ctx 5)` dominates `(span 5, ctx 3)` | **PARTIAL** | The dominance reproduces on **both** corpora, mine, today — doc corpus: span1 ctx5 = **2.15%** vs span5 ctx3 = **2.46%** (and the run reproduces the doc's published 25.84%/3.82%/6.60%/2.46% to the digit); first-party: **0.35%** vs **0.38%**. Context costs a fixed 32-hex hash at any width; anchor lines cost stored bytes forever — so it is better *and* cheaper. **What the sweep does not measure is edit-robustness**, which is the feature's actual purpose: a 5-line context needs 5 unchanged neighbours for the filter to fire, and with span 1 the filter is consulted 4× as often (25.8% vs 6.6% non-unique). The constants must be re-derived; adopting `(1,5)` on this metric alone would repeat the error of tuning one dial on one axis. |
| **FATAL-3c** `MAX_BYTES_READ` derived on the wrong population | **CONCEDE** | Over the population that matters — files the `file` column names — **4 of 2,919 row-weighted references exceed 1 MiB (0.14%, 7× the quoted 0.02%)**, largest `benchmarks/nightly/history.jsonl` at **25,178,665 bytes**, then `CHANGELOG.md` at 2,427,521. **195 of 2,919 references (6.7%) carry an extension outside the census** (154 `.svelte`, 17 `.json`, 10 extensionless). |
| **FATAL-3d** `MIN_ANCHOR_CHARS = 24` is placed, not derived; unit mismatch | **CONCEDE** | The published table's buckets are 10 wide and §6.2 reads the knee as "20–30", so any value in 20…29 is equally supported; and the table measures **one** normalized line while Р6 applies the threshold to **the whole anchor body** (up to five lines). |
| **SERIOUS-1a** §7.4's gate is failed by §7.5's own pessimism; "2–8%" is one cell of four | **CONCEDE** | Arithmetic on v3's own table: 125 ms × 1.25 = 156 ms, so +100 ms is **+80%**; 303 × 1.25 = 379, so +100 ms is **+33%**. The 2–8% ratio is 0.192/8.24 … 0.671/8.24 — the **pool-600** cell; at pool 0 the same two numbers give **13.3–46.6%**. |
| **SERIOUS-1b** the form of the argument is CB-31's defect | **DEFEND** | CB-31 was per-row work added to a **read** path (`pull_next`'s candidate scan) that had none. Here the same lock already holds a per-member resolver by ratified design — `similarity._annotate_resolver` (`similarity.py:349-369`), registered at `similarity.py:376-381`, runs inside `db.txn` per member, and is what 8.24/16.23 ms per add consists of. The correct charge against Р3 is SERIOUS-2 (unbounded **time**), not CPU share. |
| **SERIOUS-2** bounded in bytes, unbounded in time | **CONCEDE** | `S_ISREG` closes two enumerated members (FIFO, device) of a class whose remaining members are regular files on slow/hung mounts, plus `realpath`'s per-component `stat`s. The consequence chain checks out in code: `db.connect()` sets `busy_timeout=5000`; SQLITE_BUSY is **5**, and `_ENVIRONMENTAL_CODES = frozenset({8, 10, 13, 14})` (`db.py:483`) so it is never converted to `TrackerUnwritableError`; `_cmd_add` catches only `json.JSONDecodeError` (re-raise) and `ValueError` — a concurrent writer therefore gets a **raw traceback**, not a diagnosed refusal. §4.3 has no `timeout` token. |
| **SERIOUS-3** `path` — the field that selects the file — has no invariant | **CONCEDE** | Р2's INVARIANTS block validates `v`, `line`, `end`, the span, both hashes, `text` bytes and `captured_at_commit`, and never mentions `path`. The write side really is open: `_validate_meta_keys` checks key **names** only (`findings.py:260-289`) and the merge is a bare `new_meta.update(meta_update)` (`findings.py:2020-2021`). Р7 step 1 cites **§7.1** (byte/kind caps) and not §7.2 (containment), so whether resolution re-contains an attacker-written `path` is left to the reader. |
| **SERIOUS-4** "never `resolver_errors`" is a sentence, and the vocabulary is not total | **CONCEDE**, and the residue is worse than stated | `db.run_pre_add_resolvers` wraps every call in `except Exception` and stamps `patch["resolver_errors"]` (`db.py:449-460`). Two things the finding gets right and one it understates: the claimed precedent does **not** exist — `similarity._annotate_resolver` catches nothing at all and relies on the runner's swallow; and `_validate_resolver_outcome(outcome, …)` is called **inside** the same `try` (`db.py:436`), so even a `loc.py` that catches every I/O error of its own still lands in `resolver_errors` if its returned patch fails validation — a residue no amount of internal catching closes. `internal_error` in the dictionary plus a "capture never raises" test in Т-a is the minimum. |
| **SERIOUS-5** the path cache is unspecified, and `probe_cost.py` prints its own conclusion | **CONCEDE** | `MEASUREMENTS:1589-1591` really is a comment asking *"how many DISTINCT files does a 100-member batch typically touch?"* followed by two literal `print()`s asserting the answer, with no computation. My re-derivation of the real ratio: 40 filing groups of ≥10 rows, **745 rows → 416 distinct paths = 56% distinct**, with `(37,28)` and `(31,29)` on the two largest diverse groups — Opus's numbers exactly. |
| **SERIOUS-6** `realpath`-then-`open` is still check-then-open | **CONCEDE** | v3 delivers `fstat`-on-the-opened-object; containment is still decided from a pathname before the open. Closable in stdlib (`O_NOFOLLOW` + `readlink("/proc/self/fd/N")`), so "declare it as half a boundary" is the floor, not the ceiling. |
| **SERIOUS-7** `text` is stored, capped, argued about, and never read | **CONCEDE** | Р7 resolves on `hash` and `context_hash`; `anchor_resolve`'s response shape carries no `text`; no section names a consumer. It is simultaneously the largest field, the whole of §2.2's byte-cost row, and the entire payload of the exfiltration channel (Codex F6). Naming a reader or deleting it is one line either way. |
| **SERIOUS-8** the load-bearing dialect rule is a minority dialect in codebugs | **PARTIAL** | The presentation is wrong: autosorter 609/709 = 86% vs codebugs 23/61 = 38% are read as one agreeing fact. But the rule's actual **justification** is the agreement rate (22/23 and 41/47), which is corpus-consistent, and Opus's own measurement — all 7 single-name disagreements resolve in the column's favour, 0 rows lost — supports it. Fix the sentence; keep the rule. |
| **SERIOUS-9** basename equality is not corroboration | **CONCEDE** | Reproduced exactly, 2 rows in autosorter and 0 in codebugs: **CB-2944** column `src/autosorter/core/entities.py`, token `cli/commands/entities.py`; **CB-2959** column `src/autosorter/core/db/entity_corrections.py`, token `core/entity_corrections.py`. CB-2944 is a genuine different-file pass, i.e. a *confidently wrong* anchor — the outcome Р7 is at pains to avoid for `ambiguous`. The suffix-match fix is cheap and Codex found the same hole independently. |
| **W-1** §4.1's table does not partition | **PARTIAL — one sub-claim REFUTED** | **Refuted:** the 3 non-int `line` values are int-strings `"51"`/`"13"`/`"45"` (CB-1886/1875/1873), which B1 explicitly covers — they are **not** B4, and the fix list must not move them. **Conceded:** the `+N` convention does mean codebugs elsewhere in that same row set (I verified `lines` B3 = autosorter 52 / codebugs 32 and `lines` B4 codebugs 10), and codebugs carries **zero** `line` rows, so `95+3` is unreadable under the table's own convention; codebugs `sites` B3 = **6** is absent from the table; and the 2 dict-valued `sites` rows appear in both the B3 and B5 cells. |
| **W-2** "15/15" over the wrong denominator | **CONCEDE** | `site` = 16 rows: 15 token-bearing, 1 prose. |
| **W-3** 2048 justified as parity with 2000 | **CONCEDE** | `_OCC_DESC_CAP = 2000` (`findings.py:405`). The clause creates the inconsistency its own sentence forbids. |
| **W-4** §7.6 promises a two-read check Р7 does not contain | **CONCEDE** | Р7 reads once. Either re-stat after reading and compare, or say TOCTOU is accepted unmitigated. |
| **W-5** `captured_at_commit` has no definition and no reader | **PARTIAL** | The definition gap is real (capture-time `HEAD`? `reported_at_commit`? `_effective_commit`? what does a dirty tree store?) and must be closed. But "no reader" is an argument for **defining** it, not deleting it: §5's backfill and the deferred C2 are its readers by construction, and it is 40 bytes. |
| **W-6** resolution cost never measured | **CONCEDE** | Р7 scans the whole file and hashes every window; §5 defers the resolution cache *"по замеренной цене"* of a cost nobody measured. The document measured the cheap side to three digits and the expensive side not at all. |
| **W-7** `anchor_resolve`'s filters inherit CB-19/CB-25 | **CONCEDE** | `status`/`category` are vocabulary filters and the doc specifies neither resolution nor the empty-filter convention; the `check_findings` precedent it copies maps `None`/`""` to `"open"`. Cheap to state, expensive to discover in Т-b. |
| **W-8** the owner's *auto*-resolution request was narrowed | **PARTIAL** | The architectural reasons for deleting `get(resolve_anchor=)` and `staleness_check(anchor=)` are correct and both attackers accept them — **DEFEND the decision**. But CB-95 verbatim is *"Ideally, with auto-resolution of the new line"*, and a narrowing of the request's **meaning** belongs in a question under the owner's own standing rule, not in a §10 bullet — **CONCEDE the disclosure**. |
| **W-9** `sites_dropped` is a count without identity | **CONCEDE** | §5 makes its *distribution* the trigger for the multi-anchor table; a histogram cannot answer which places were lost. Compounded by Codex D6 (the same counter also counts truncated lines). |
| **W-10** the cost probe does not implement the specified capture | **CONCEDE** | `capture_once` (`MEASUREMENTS:1538-1549`) is `open` + `read` + `decode` + `strip` + two `sha256` — no `realpath`, no `stat`/`S_ISREG`, no NUL sniff, no indent token, no whitespace collapse, no grammar, no root discovery. I measured the single omitted item that dominates: **`git rev-parse --show-toplevel`, warm, median 1.064 ms / p95 1.344 ms over 50 runs** — 5.5× the *entire* claimed capture, and `provenance._repo_root` (`provenance.py:59-72`) is exactly that call. |
| **N-1 / N-3** `out_of_repo` collides with provenance's token | **CONCEDE** | `provenance.py:345` returns `f"out_of_repo (worktree {root})"` — same word, different shape, and §4.3 calls its dictionary closed. |
| **N-2** the calibration script's normalizer is not Р6's | **CONCEDE** | `re.sub(r'\\s+', ' ', s.strip())` (`MEASUREMENTS:1641`) is a raw string: it matches a literal backslash followed by `s`, so internal whitespace is never collapsed. Opus measured the numeric effect as nil and so do I (my re-runs used the same literal-backslash normalizer and reproduce the doc's figures to the digit) — the finding is about the artifact's claim to be re-executable against the spec, and it stands. |
| **N-4** `unreachable_commit` is outside the closed dictionary | **CONCEDE** | §4.3 lists 13 tokens; §5 introduces a 14th. Codex found this independently. |
| **N-5** `out_of_range` is justified by a file `too_large` refuses first | **CONCEDE** | Measured: the 135,926-line file is `actions-runner/_work/_actions/actions/upload-artifact/v4/dist/upload/index.js` at **5,051,718 bytes** — 4.8 MiB, refused by the 1 MiB cap. (It is also, itself, a vendored CI artifact — FATAL-3a in miniature.) |
| **N-6** three significant figures from one run per cell | **CONCEDE** | `MEASUREMENTS` §10 item 5 discloses it; v3 quotes `16.23 ms` / `1337 ms` and does not. |
| **CA — mandatory fix #3: Т-c still `опционально`** | **CONCEDE** | §9 still reads *"Т-c — backfill dry-run через `git show`, **опционально**"*. Third consecutive round; the judge's condition was explicit — reject per-observation capture **and** Т-c becomes required. |
| **CA — mandatory fix #2 deliberately reversed** | **DEFEND** | See SERIOUS-1b. The reversal is measurement-backed and structurally right; its supporting arithmetic is not, and that is what must change. |
| **CA — MCP/CLI contract incomplete** | **CONCEDE** | Per-record shape is given; outer envelope, missing-id behaviour, filter defaults, CLI output and exit codes are not. Same finding from both families. |
| **CA — formal grammar gaps** | **CONCEDE** | Sign/zero/descending ranges, nested-list recursion depth, duplicate sites, and the exact `sites_dropped` counting rule for B5 are unstated. |

## B. Codex(Sol) findings

| # | ruling | evidence |
|---|---|---|
| **F1** anchorable population wrong by >4× | **CONCEDE, with the number corrected** | Codex's own figure (677) is the **upper bound** — "has a source key AND the column is a readable regular file"; I reproduce it exactly (autosorter 677, codebugs 62). The number that answers the owner's question is one predicate stricter. See "the three numbers". |
| **F2** capture has no correct worktree-root carrier | **CONCEDE** | Duplicate of Opus FATAL-1; corroborated across families, verified in code. Codex's added point is right too: deriving the root from the connection yields **main's** checkout, which is exactly the divergence v3 cites as its reason to reject `describe_root()`. |
| **F3** the cost measurement does not measure the proposed capture | **CONCEDE** | Duplicate of Opus W-10, and Codex's `git rev-parse` measurement (median 1.264 ms) reproduces on my machine at 1.064 ms. Same conclusion from an independent runtime. |
| **F4** v3's arithmetic violates its own 25% gate | **CONCEDE** | Duplicate of Opus SERIOUS-1a; both families derive +80% at pool 0 and +33% at pool 200 independently, and both land on 46.6/47% for the shallow-pool capture share. |
| **F5** "cache by path per batch" is not implementable in the existing seam | **PARTIAL — the impossibility claim is refuted** | The seam facts are right: `fn(conn, observation)`, invoked once per member (`findings.py:1052`), no batch object, no start/end callback. But "not implementable" overreaches: a **module-level cache keyed on `(realpath, st_mtime_ns, st_size)`** needs no seam change, is correct across batches, worktrees and long-lived servers, and reuses the `stat` the `S_ISREG` check already requires. The real finding is Opus SERIOUS-5's: unspecified scope, lifetime, invalidation key and memory bound. Do not let "impossible" push the design back into a two-phase carrier it just paid to delete. |
| **F6** any MCP `add` caller gains an in-repository file-read oracle | **CONCEDE — verified end to end, and the text IS returned** | The MCP tool returns `add_finding(...)` whole (`findings.py:2988-3001`); `_finalize_add` (`findings.py:1156-1196`) builds the response from the inserted row, and the inserted row is re-read with `SELECT *` **after** the resolver patch was merged into `meta_final` (`findings.py:1052-1099`), so `meta.loc.text` reaches the caller — the docstring already advertises `meta.similar_to` arriving by exactly that path. `<root>/.git/config` is a 546-byte regular file whose `realpath` is inside the root: it passes every stated predicate. §7.3's owner question asks only about paths **outside** the worktree, so the capability expansion the caller actually gains is not on the page. Deleting `text` (Opus SERIOUS-7) closes the payload; an allowlist or a `.git`/dotfile denial closes the read. |
| **F7** a regular file can monopolize the write lock | **CONCEDE** | Duplicate of Opus SERIOUS-2, same chain, verified. |
| **S1** resolver exceptions become `resolver_errors`, contradicting the persisted-failure contract | **CONCEDE** | See Opus SERIOUS-4 for the ruling and the residue (`_validate_resolver_outcome` is inside the swallowed `try`, so the promise cannot be kept by `loc.py` alone). |
| **S2** full-path `realpath` does not close the symlink race | **CONCEDE** | Duplicate of Opus SERIOUS-6. |
| **S3** deleting C2 leaves an uncomputable trigger for rebuilding C2 | **PARTIAL** | Correct that §5's trigger is **not computable from `anchor_resolve`'s output**: `lost` carries `reason=null` by Р7's own table and A cannot tell an edited anchor line from a deletion, a wrong coordinate or a corrupt capture. Not correct that it is uncomputable in principle — a one-off offline probe over `captured_at_commit..HEAD` for the `lost` rows answers it without shipping C2. §5 promises triggers *"вычислимые на живом трекере"*, so the fix is to say which instrument computes this one. |
| **S4** the normalizer can report semantically edited code as unchanged | **PARTIAL** | The mechanics are right: `indent // 4` maps 4 and 7 leading spaces to depth 1, and whitespace collapse makes `return "a  b"` and `return "a b"` one hash. But collapsing whitespace is the *intended* robustness of a normalizing anchor, and matching through a formatting-only edit is the feature. What is falsified is Р6's own justification sentence — *"реиндент — это правка, про которую честнее сказать `moved`"* — which holds only when the reindent crosses a 4-column boundary. Fix the claim, not the normalizer. |
| **S5** two contradictory ambiguity algorithms | **CONCEDE** | Р7's prose (*"из выживших выигрывает ближайший к `line`, но только если он единственный"*) admits a reading under which a uniquely-nearest survivor wins, while the table makes any `>1` survivors `ambiguous`. And zero survivors *after* the context filter has no row at all — the `lost` row is written against zero **hash** candidates. |
| **S6** update still permits a validly shaped forged anchor | **PARTIAL** | Real gap in §10.2's declaration, which concedes only that **malformed** garbage is stored and read as `invalid_anchor`; a well-formed forged `loc` defeats Р4's stated anti-spoofing reason and is read as `current`. Severity is disclosure, not privilege: `update_finding(meta_update=)` is an authorized write path and `restore_findings` bypasses validation by design. One clause in §10.2. |
| **S7** "every insert" is false | **CONCEDE** | The firing predicate is `finding_id is None and annotate` (`findings.py:1052`), so explicit-id inserts, `import_findings` (`annotate=False`) and `restore_findings` (raw INSERT) create rows with neither an anchor nor a refusal object. §2.2's "популяция записи — каждая ВСТАВКА" and §5's lost-rate denominators must name the real population. |
| **S8** basename corroboration admits contradictory paths | **CONCEDE** | Duplicate of Opus SERIOUS-9; found independently by both families, with the same corpus reproduction. |
| **S9** recapture is a named verb without failure semantics | **CONCEDE** | `anchor_recapture` appears three times and never says whether it writes via `update_finding(meta_update=)` or its own UPDATE, whether transient failure replaces a good anchor with a refusal object, whether it overrides `loc: null`, or whether it CASes against a concurrent write. That decides whether the repair path can destroy the last good anchor — and whether `loc.py` stays zero-SQL like `similarity.py`. |
| **D1** file-size calibration samples the wrong population (`.jsonl` excluded) | **CONCEDE** | Verified: the live tracker references `benchmarks/nightly/history.jsonl` at 25,178,665 bytes, an extension the census never sees. Same root cause as Opus FATAL-3c; the two families found it from opposite ends. |
| **D2** `os.path.isfile` was mislabeled "anchorable" | **CONCEDE** | This *is* FATAL-2's mechanism, stated as a methodology defect: `isfile` follows symlinks and tests neither containment, grammar, size, NUL content, line range nor minimum anchor length. |
| **D3** every-20th sampling is systematic, not random | **PARTIAL — measured, small, and directional** | I ran all 20 phases on the first-party corpus: over **all** positions non-uniqueness is 29.43% and still-ambiguous-after-ctx3 is **1.49%**; the per-offset ranges are 28.36–30.52% and **1.37–1.55%**; **phase 0 — the one the script samples — sits at the bottom of both ranges (28.57% / 1.37%)**. So the criticism is right in kind and direction (the published figure is biased low by ~8% relative), and wrong in importance: ~0.1 pp absolute, changing no decision. `MEASUREMENTS` §10 item 4 already discloses the sampling; it should disclose the **phase**. |
| **D4** timing evidence has no distribution | **PARTIAL** | Disclosed in `MEASUREMENTS` §10 item 5 (*"одним прогоном на точку… опираться следует на ПОРЯДОК"*) — so it is a named limit, not a hidden one. The defect is that §7.4 then builds a **landing gate** on those single-run, machine-specific absolutes, which the disclosure explicitly says not to lean on. The gate needs a re-measured baseline on the landing machine, or an absolute per-member budget instead. |
| **D5** the corpus is mutable and unauditable | **PARTIAL** | Overstated: the scripts are committed verbatim and **three independent parties re-ran them today** — Opus, Codex and I each reproduce every §1а cell modulo drift, and my run reproduces §6.2's uniqueness table to the digit. The legitimate residue is narrow and real: no CSV hash or row-count stamp was recorded, and the corpora moved 129 → 131 → **138** codebugs rows in two days, so *exact* historical denominators cannot be re-derived. Stamp the export (`sha256` + row count + timestamp), do not re-litigate reproducibility. |
| **D6** `sites_dropped` conflates different losses | **CONCEDE** | §4.3 defines it as atomic places seen and not anchored; Р6 also increments it when a declared range is truncated to five lines. "One other site" and "86 discarded lines of the chosen site" become the same integer — inside the counter that is §5's trigger. |
| **M1** capture binding contract | **CONCEDE** | The requirement follows from F2/FATAL-1 and is the structural fix. |
| **M2** bounded-lock contract + concurrency acceptance test | **CONCEDE** | Follows from F7/SERIOUS-2; there is no test today that a second writer survives a slow capture. |
| **M3** atomic safe-open mechanism | **CONCEDE** | Follows from S2/SERIOUS-6; stdlib-reachable, so "declared limit" is a floor. |
| **M4** closed exception-to-reason table | **CONCEDE** | Follows from S1/SERIOUS-4; must include the `_validate_resolver_outcome` residue, which no `except` in `loc.py` can reach. |
| **M5** grammar fallback rules | **CONCEDE** | Invalid higher-priority key vs valid lower key is genuinely unspecified, and §4.2's order is stated as a total order with no fallback. |
| **M6** stored-object validation completeness | **CONCEDE** | `context_before`/`context_after` are described as *"ФАКТИЧЕСКИЕ ширины"* in Р2 and appear in no invariant — no type, no sign, no `≤ CONTEXT_LINES`. Round-2 W-1's residue; still open. |
| **M7** resolution/API completeness | **CONCEDE** | Duplicate of Opus's CA row plus S5. |
| **M8** computable deferred-feature metrics | **CONCEDE** | Duplicate of S3 + N-4 + the lost-rate denominator half of S7. |
| **CA — `locs:[…]` still unevaluated while the doc calls a table the only multi-anchor answer** | **PARTIAL** | §2.2's row does read *"(c) N — единственная, решающая мульти-якорь"*, which is an overstatement — a bounded top-level list is a third shape and is not compared. But deferring it behind a computable trigger is legitimate and Opus rated the same row acceptable. One word, plus one line naming the unevaluated option. |
| **CA — failure vocabulary REWORDED-ONLY** | **CONCEDE** | Same as S1/SERIOUS-4/N-4. |

---

## The three population numbers, and which one the fix must state

The holder measured **675/3300**, Codex **677/3303**, Opus **656/3301**. All three are correct
answers to *different* questions, and I reproduce two of them exactly on today's export:

| predicate | autosorter | codebugs |
|---|---|---|
| rows carrying a locational key (`function` and the peer's `anchor` excluded) | 709 | 62 |
| **+ the `file` column is a readable regular file** → **upper bound** | **677** (holder's 675 on the 3300-row snapshot) | **62** |
| **+ the value actually yields a place under §4's grammar** → **what gets an anchor** | **656 = 19.9%** | **47 = 34.1%** |

So the holder and Codex differ by live drift alone (three rows in two days); Opus differs by one
predicate — whether the value **parses**. **State the grammar-level number**, because that is the
one that answers "will this card get an anchor", and show the upper bound beside it so the two
cannot be confused again. And say which export it was taken on, with its row count — the corpus
moved twice during this review.

## Where the attackers disagree — recorded rather than merged

1. **`locs:[…]` / the storage table.** Codex rules it STILL OPEN ("the bounded top-level multi-anchor
   object remains unevaluated"); Opus rules it "DEFERRED with a trigger — acceptable". I split it:
   the deferral is fine, the word *"единственная"* is not.
2. **The path cache.** Codex calls it **not implementable** in the existing seam and proposes
   restoring a two-phase `CaptureContext`; Opus calls it implementable-but-unspecified (module-global
   state) and asks for scope/lifetime/bound. Opus is right, and the difference matters because
   Codex's version would reverse the single best decision of the rebuild.
3. **The cost share.** Codex quotes 1.2–46.6% (using pool 1200 for the low end), Opus 13–47% at pool
   0 and 2.3–8.1% at pool 600. Both endpoints are arithmetically right; the honest statement is a
   per-pool range, not either summary.
4. **Round-2 residue.** Codex marks "manual updates persist malformed anchors" STILL OPEN; Opus
   marks it DECLARED-NOT-CLOSED (legitimate as a named limit) except for `path`. Opus is right on
   the general case and Codex is right on the narrower one it found (a well-formed **forged** anchor
   is outside the declaration's wording).
5. **W-1's B4 claim** is Opus-only and is the one finding in this round that is factually wrong.

---

## What actually has to change

Ordered by how structural the change is: a ratified decision first, a number second, wording last.

### I. Changes to a ratified decision or to a decision the owner is being asked to ratify

1. **Name the capture root, or admit the signature change** (Opus FATAL-1, Codex F2). The seam
   carries no root and cannot; the process cwd is not the worktree — `provenance._ambient_cwd`'s own
   docstring is about exactly this. Minimum honest fix: stamp `loc.root` at capture and refuse at
   resolution when it differs (fail-closed). The headline *"ноль правок сигнатур ядра"* does not
   survive this, and the owner should read that in v3, not discover it in Т-a.
2. **Answer the in-repository read question, not only the out-of-repo one** (Codex F6). Any MCP
   `add` caller can name `.git/config` or an untracked `.env`, and the normalized bytes come back in
   the add response. §7.3's question as written does not cover it. Either delete `text` (which also
   discharges Opus SERIOUS-7 and most of §2.2's byte-cost argument), or add a denial list /
   tracked-files restriction, or put the capability in front of the owner as its own question.
3. **Bound capture in TIME, not only in bytes** (Opus SERIOUS-2, Codex F7/M2). `S_ISREG` closes two
   members of a class whose survivors are all regular files; a five-second block turns every
   concurrent writer's add into a raw `sqlite3.OperationalError` traceback. Add a wall-clock budget,
   a `timeout` token to §4.3, and the concurrency test to Т-a. **Do not** reverse Р3's placement to
   get this — see the DEFEND above.
4. **Re-derive the calibration constants, and disclose the corpus in v3 itself** (Opus FATAL-3).
   The 52,261-file corpus is 93.2% vendored/CI/build; `CONTEXT_LINES` was never swept; my sweep
   shows `(span 1, ctx 5)` beating `(span 5, ctx 3)` on both corpora while storing four fewer lines
   per card forever. Sweep both dials, on first-party source, and state what the winning pair costs
   in edit-robustness — the axis neither sweep measures.
5. **Make Т-c required, or state in §5 that every trigger is computable over new inserts only**
   (Opus CA / judge's mandatory fix #3, open for the third round). This is a condition the judge
   attached to rejecting per-observation capture, and v3 took the rejection without the condition.
6. **Give `path` an invariant and make the vocabulary total** (Opus SERIOUS-3/SERIOUS-4, Codex M4/M6).
   `path` selects the file that gets opened and is the one field with no rule; add
   `internal_error`; and note the residue that `loc.py` cannot catch (`_validate_resolver_outcome`
   runs inside the runner's own swallow).
7. **Specify `anchor_recapture` before it is a named verb in the cut** (Codex S9, Opus W-6-closure):
   transaction phase, whether failure may replace a good anchor, whether it overrides `loc: null`,
   and whether it writes through `update_finding` or its own SQL.

### II. Changes to a number

8. **§2.1's capability cell and §8 q1's cost line** become the anchorable population:
   **656/3303 ≈ 20% autosorter, 47/138 ≈ 34% codebugs**, with the three-way split (no key /
   unusable value / unreadable file) and the 677/62 upper bound shown beside it. Stamp the export.
9. **Publish the cost share as a range, and fix the gate** (Opus SERIOUS-1a, Codex F4):
   13–47% at pool ≈0, 2–8% at pool 600; §7.5's own +100 ms pessimism is **+80%** and **+33%** against
   §7.4's 25% gate at the two shallow pools. Either re-baseline the gate per pool depth, or replace
   the relative gate with an absolute per-member budget.
10. **`MAX_BYTES_READ` re-derived on the referenced-file population**: 4/2,919 references over 1 MiB
    (0.14%), max 25 MB, and 195 references (6.7%) whose extension the census never measured.
11. **The batch-cost premise**: the measured distinct-path ratio over real filing groups is
    **56%** (745 rows → 416 paths), not "few distinct paths", and `probe_cost.py`'s last two lines
    must stop printing a conclusion the script does not compute.
12. **The capture cost is not 0.192 ms end to end** (Opus W-10, Codex F3): root discovery alone is
    `git rev-parse --show-toplevel`, warm median **1.06 ms** here / 1.264 ms on Codex's runtime, and
    the probe omits `realpath`, `stat`, NUL scan, the indent token, whitespace collapse and the
    grammar. Re-measure the specified capture or mark the figure a floor for a simpler function.
13. **`MIN_ANCHOR_CHARS`**: either derive it at bucket resolution finer than 10, or say plainly that
    any value in 20–29 is equally supported — and fix the unit (the table measures one line; the
    constant caps the whole anchor).

### III. Wording, scope and artifact fixes

14. **§2.2's "популяция записи — каждая ВСТАВКА" is false** (Codex S7): the resolver fires only on
    `finding_id is None and annotate`, so explicit-id inserts, import and restore get neither an
    anchor nor a refusal object. Fix the row and every lost-rate denominator that depends on it.
15. **B3's basename gate** becomes a suffix match when the token carries a directory (Opus
    SERIOUS-9, Codex S8): measured cost 2 rows refused (CB-2944, CB-2959), 16 kept.
16. **§4.1's table must partition** — but the 3 non-int `line` values are **int-strings and stay in
    B1**; what needs fixing is the `95+3` notation (codebugs has no `line` rows), the missing
    codebugs `sites` B3 = 6, and the dict-valued `sites` rows counted in both B3 and B5.
17. **Р7 prose vs table** (Codex S5): decide whether a uniquely-nearest survivor wins or any `>1`
    survivors are `ambiguous`, and give zero-survivors-after-context a row.
18. **§7.6 vs Р7** (Opus W-4): either re-stat after reading and compare, or say TOCTOU is accepted
    unmitigated. **§7.2 vs Р7 step 1**: say that resolution re-applies containment to the stored,
    caller-writable `path`.
19. **`captured_at_commit`**: define it (capture-time `HEAD`? dirty tree?) and name its reader.
20. **`MAX_TEXT_BYTES`**: 2000, or a stated reason for 2048 — the parity clause currently creates
    the inconsistency it forbids.
21. **`out_of_repo`** collides with `provenance.py:345`'s payload-carrying token; **`unreachable_commit`**
    is outside §4.3's "closed" dictionary; **`sites_dropped`** conflates other-places with
    truncated-lines and records no identity; **`context_before/after`** have no invariant.
22. **Say the request was narrowed** (Opus W-8): CB-95 asked for auto-resolution; v3 deleted both
    auto surfaces for correct reasons. That belongs in q1 or a q5, not in a §10 bullet.
23. **§4's dialect sentence** must stop pairing 86% (autosorter) with 38% (codebugs) as one agreeing
    fact; the rule's justification is the agreement rate, which does hold in both corpora.
24. **Р6's reindent claim** is false below a 4-column step (`indent // 4`), and whitespace-only edits
    are invisible by construction — say both.
25. **Artifact hygiene**: `probe_calibrate2.py`'s `SKIP` misses `.venv312`/`venv`/`actions-runner`/
    `build`; its normalizer (`r'\\s+'`) is not Р6's; sampling is phase-0 systematic (measured bias
    ~0.1 pp, low side); §10 should stamp the exports with a hash and row count; §5's C2 trigger
    should name the offline probe that computes it.
26. **§2.2**: drop *"единственная"* from the (c) row and name the unevaluated bounded-`locs[]` shape.
27. **N-5**: the 135,926-line file is 4.8 MiB and `too_large` refuses it first — justify
    `out_of_range` with a file the byte cap admits.
