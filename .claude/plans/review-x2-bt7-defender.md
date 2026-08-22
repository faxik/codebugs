# BT-7 «Локационный якорь» — defence of the design (adversarial-review-x2, defender pass)

Target: `.claude/plans/BT-7-location-anchor.md` (v1, DIR-2, 2026-08-22)
Attackers: `review-x2-bt7-opus-attack.md` (FATAL-1..8, SERIOUS-1..12, W-1..10, N-1..4)
and `review-x2-bt7-codex-attack.md` (numbered here C-R1..12, C-S1..10, C-M1..9, C-A1..4
in order of appearance).

**Method.** Every defence below was verified by running or reading the actual tree, never by
reasoning from the document. Two live executions were run against an in-memory database (the
repo's own tracker was touched only through the read-only `export-csv`). Both corpora were
re-measured with one script over `/home/faxik/.cache/codebugs-identity/corpus.csv` (3176 rows,
snapshot dated **2026-08-17 09:57**) and a fresh read-only `codebugs export-csv` of this
tracker (128 rows).

**Defender's overall verdict: the DIRECTION survives, the DOCUMENT does not.** Content anchor
+ resolve-on-read + not-an-identity-input is the right shape and no attack broke it. But the
central mechanism (Р3(i)+Р4) is *provably impossible* — I proved it by running it, not by
reading it — and the capture grammar (§4) is derived from a misread writer contract and from a
key census that missed the second-most-common locational key in **both** corpora.
**55 CONCEDE / 13 PARTIAL / 1 DEFEND across 69 findings.** A defence rate that low is itself
the finding: this document asserted its measurements instead of holding them, and both
attackers walked in through the same door.

---

# Opus adversary

## FATAL-1: CONCEDE — and it is worse than the attacker showed, because I ran it

**Evidence (executed, not read).** With an anchor resolver registered exactly as Р3(i)
specifies:

```
$ uv run python -c "... db.register_pre_add_resolver('anchor.capture', _r, meta_keys=('anchor',)) ..."
reserved: ['anchor', 'resolver_errors', 'similar_to']
updatable: ['similar_to']
ADD REFUSED: meta keys ['anchor'] are reserved for the identity machinery
             (they are its output, not input — strip them before re-submitting)
```

Second run, with `updatable_keys=("anchor",)` added:

```
added meta: {'category_minted': True, 'anchor': {'v': 1, 'line': 7}}
after update: {'category_minted': True, 'anchor': {'v': 1, 'line': 99}}
ADD REFUSED: meta keys ['anchor'] are reserved for the identity machinery
```

So all three of the attacker's sub-claims hold, and the third is the sharpest:

- `findings.py:277-283` — `reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()`,
  and `db.resolver_reserved_meta_keys()` (`db.py:331-343`) is the union of every registered
  resolver's `meta_keys`. Registration IS reservation. Р4's «`anchor` — НЕ в
  `_RESERVED_META_KEYS`» is true of the literal frozenset at `findings.py:235` and false of the
  behaviour, which is what a ratification line must be right about.
- `meta_keys=()` is not an escape: `_validate_resolver_outcome` (`db.py:361-376`) computes
  `bad = (set(outcome) - resolver.meta_keys) | (set(outcome) & forbidden)` and raises.
- «caller-supplied побеждает» is backwards. `findings.py:1049` is
  `meta_final.update(db.run_pre_add_resolvers(...))` — the resolver patch is the RIGHT-hand side
  of a `dict.update`. The `fingerprint` analogy Р4 leans on is the opposite shape:
  `findings.py:937-938` is `if fingerprint is None: fingerprint = _derive_fingerprint(...)`,
  i.e. the CORE enforces caller-wins. Nothing does that for meta.

**Fix.** Rewrite Р4 as a single ratifiable line and delete the `fingerprint` analogy:

> **Р4. Резервирование.** ★ Регистрация резолвера ЕСТЬ резервирование (`db.py:331-343` →
> `findings.py:277-283`), поэтому `anchor` автоматически зарезервирован на ADD — вызывающий НЕ
> может передать его в `add(meta=)`, и это правильно: захват серверный, caller-supplied якорь
> был бы спуфингом координаты. Ремонтопригодность достигается ЕДИНСТВЕННЫМ способом, который
> даёт этот шов: `updatable_keys=("anchor",)` при регистрации — ровно как `similarity.py:376-381`
> для `similar_to`, с той же причиной CB-26. Тогда Р3(iii) (`update_finding(meta_update=…)`)
> работает, а Р3(i) не конфликтует. Проверено исполнением, а не чтением.

And in §9, Т-a's brief must name `updatable_keys=("anchor",)` explicitly — CB-45's own lesson is
that a property asserted in prose and not at the registration site is a property that does not
exist.

---

## FATAL-2: CONCEDE

**Evidence.** `/home/faxik/.claude/skills/libcheck/SKILL.md:135`, in the `batch_add` filing step:
`` `meta`: `{"lines": [line_numbers], "confidence": "high"|"medium"} `` — a list of INDIVIDUAL
line numbers, produced per-pattern from a whole-file read (`SKILL.md:125-136`). Re-measured over
corpus A, all 206 all-int lists:

| len | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 13 | 16 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rows | 40 | 76 | 42 | 25 | 8 | 2 | 3 | 3 | 1 | 3 | 1 | 1 | 1 |

Total **206** (not «≥210»), of which len 6..20 = **15** (not 19). §4's rule reads the 76 length-2
lists as `[min, max]`. On a `[56, 106]` that anchors, hashes and reports a 51-line block as *the*
location — a confident wrong answer, which `provenance.py:41-52` names as the one failure this
module has been patched for three times.

**Fix.** In §4 replace the list branch with:

> `list[int]` — КАЖДЫЙ элемент есть ОТДЕЛЬНАЯ строка (контракт писателя, давшего 206 из 206
> списков: `libcheck/SKILL.md:135`, `{"lines": [line_numbers]}`). Диапазон из пары НЕ выводится
> ни при какой длине. Берётся ПЕРВЫЙ элемент как одиночная строка; остальные считаются в
> `anchor_sites_dropped` (см. фикс FATAL-4/W-10 — метрика для решения о `meta.anchors`).

and delete the words «берётся `[min, max]` если длина 2 и значения упорядочены».

---

## FATAL-3: CONCEDE

**Evidence.** Re-measured the leading `NAME.ext:DIGITS` token of every string `meta.lines`
against that row's `file` column:

| corpus | rows with such a token | exact match with `file` | basename-only | neither |
|---|---|---|---|---|
| codebugs (B) | 31 | **2** | 29 | 0 |
| autosorter (A) | 38 | **0** | 31 | 7 |

(My basename/neither split differs slightly from the attacker's 38/0 for corpus A — 7 tokens
match neither. The load-bearing number — **exact matches: 0 and 2** — reproduces exactly.)

The dialect is basenames (`findings.py:142`); the `file` column holds repo-relative paths
(`src/codebugs/findings.py`). §4's gate («только если `file`-префикс совпадает с колонкой
`file`») therefore captures 2 rows in one corpus and none in the other, while citing "41 + 33" as
the reason to build it.

**Fix.** Two edits. In §1а, correct the claim to «41 + 33 строк НЕСУТ `file:N` токен, но
совпадение с колонкой `file` — 2 из 31 (codebugs) и 0 из 38 (autosorter): диалект — БАЗОВЫЕ
ИМЕНА, колонка — путь от корня». In §4, either delete the branch, or gate it on
`os.path.basename(file_column) == token` **and refuse when more than one `NAME.ext:` token is
present** (measured: 12 of 32 dogfood rows cite ≥2 distinct filenames — see W-10), because a
basename match in a tree with same-named files cannot tell which file is meant. Whichever is
chosen, the justification number in §4 must be the *matching* count, not the *token* count.

---

## FATAL-4: CONCEDE — and this is the one the document's own quoted rule should have caught

**Evidence.** Full meta-key census, both corpora:

- **codebugs (128 rows):** `notes` 98, `found_by` 69, **`lines` 44**, `assignee` 41,
  `relates_to` 32, `verified` 21, `related` 20, **`sites` 18**, `branch` 16, …
  §1а's claim «44 с локационным ключом — все под `lines`» misses 18 rows. Sample values:
  `merge.py:263-267 idempotent short-circuit (…); the acquisition path below it`,
  `milestones/triage.py:~32, milestones/capacity.py:~167`, `findings.py:432, findings.py:481`.
- **autosorter (3176 rows):** `lines` 539, `line` 98, **`sites` 30**, `function` 28, `anchor` 16,
  **`site` 13**, `location` 1. Sample `sites`:
  `entity_domain/domain.py:1123-1172; quality/remediation.py:183,269; quality/models.py:244`,
  and one value that is a JSON **list** of location strings, and one that is a **dict**.

So the real locational key set is **seven**, not the «три соседних ключа» §1а states, and the 61
rows carrying `sites`/`site` are the most unambiguously locational in either corpus.

Confirmed separately: `anchor` in corpus A holds **card ids** — three distinct values,
`CB-1878`, `CB-1566`, `CB-1895` — not «проза (имя функции / описание)» as §1а says. See SERIOUS-5
for what that costs.

**Fix.** Re-run §1а's sweep by SHAPE, not by name list, and say so in the section: the predicate
is *a meta value containing a `NAME.ext:DIGITS` token or an int-like line reference*, applied to
every key. Republish the census with all seven keys. Then §4's grammar must read `sites`/`site`
(they carry the same three forms as `lines`: `;`-separated prose, JSON list of strings, dict) or
explicitly refuse them **with the refusal counted**, because otherwise those 61 rows are silently
counted as "has no location" by the very `anchor_capture_skipped` metric §4 promises will make
the symbolic-anchor decision «ЗАМЕРЕН, а не угадан».

---

## FATAL-5: PARTIAL — the security consequence is real and must be answered; the framing is imprecise

**What holds (and must be fixed).** `provenance.file_status` really does refuse anything outside
the worktree, and the check was added under review:

```python
# provenance.py:335-345
candidate = _resolve_candidate(root, file_path)
try:
    inside = os.path.commonpath([root, candidate]) == root
except ValueError:
    inside = False
if not inside:
    return _verdict("unknown", f"out_of_repo (worktree {root})")
```

`file` is free text on a public MCP tool (`findings.py:2954`: *"meta: Optional JSON metadata
(lines, module, rule_code, etc.)"*; CB-88/CB-89 established absolute cross-repo paths,
directories, globs and prose are all in deliberate use). §7 authorizes capture on an absolute
path «если путь читаем», and Р2 stores **`text: str` — source bytes verbatim** — which lands in
the row, in `get`/`query`, and in every `export-csv`. The document names this as an honesty item
and never as a capability item. Nothing in Р2/§7 specifies a size cap, a `text` cap, a
regular-file check (`_resolve_candidate`, `provenance.py:75-96`, resolves only the PARENT
physically — safe **today only because** the containment check follows it), an encoding policy,
or a refusal of FIFO/char devices. `fsio.py:178-196` already carries exactly that node-kind
taxonomy for the WRITE direction (CB-76) and was available as precedent.

**What does not hold.** «deliberately deleting the containment check this repo added under
review» overstates: BT-7 does not modify `file_status`, and `_resolve_candidate` + `commonpath`
stay exactly as they are. What BT-7 does is open a NEW reader whose scope was never negotiated.
That distinction matters for the fix — the answer is not "restore a deleted check", it is
"declare this reader's scope", which is CB-91's open trust decision (corroborated by C-R2).

**Fix.** Add a new §7а, **«Граница чтения — отдельное решение, не унаследованный скоуп»**,
carrying: (a) the default is `commonpath` containment identical to `file_status` — capture
refuses out-of-repo and records `anchor_capture_skipped(out_of_repo)`; (b) reading outside the
worktree is CB-91's ratified-deferral decision and BT-7 does not take it — it becomes Вопрос
владельцу №4; (c) refuse anything that is not a regular file (`stat.S_ISREG`), reusing
`fsio.py`'s taxonomy; (d) numeric caps stated as numbers, not adjectives: `max_file_bytes`,
`max_span_lines`, `max_text_bytes` per anchor and per context; (e) decode with
`errors="replace"`; (f) a binary sniff (NUL in the first block) that refuses rather than hashes.
Р2's shape gains the caps as *invariants*, not as advice.

---

## FATAL-6: CONCEDE — and this is the finding that blocks the owner question outright

**Evidence (from the document itself, read end to end).**

- П3: «Якорь **ДОЛЖЕН** наследовать обе конвенции — словарь с `reason` и «новейшее наблюдение»».
- Р3(ii) ★: «на первом шаге (ii) **НЕ строить**».
- §8 q1, the package the owner answers да/нет to: «…захватывается сервером при подаче по `lines`,
  **обновляется новейшим наблюдением**, разрешается на чтение…».
- §8 q2 then asks the owner to agree to **defer** the clause q1 just asked him to ratify.

П3 is never retracted. A "yes" to q1 ratifies what the document recommends against; a "no"
rejects the four clauses it does recommend. Under the owner's standing rule («options with the
cost of each», and «вопрос собирается с нуля») this is unanswerable, and it is the load-bearing
question.

**Fix.** Three edits, and they must be made together or the contradiction survives:

1. П3 — replace «ДОЛЖЕН наследовать обе конвенции» with «наследует словарь `reason`
   безусловно; «новейшее наблюдение» — КАНДИДАТ, решается в Р3(ii), и П3 его не предрешает».
2. §8 q1 — strike «обновляется новейшим наблюдением» from the package sentence, so q1 asks
   exactly what the ★-recommendations say.
3. §8 q2 — restate as the open question it is, with both costs named: *defer* costs a
   manually-re-anchored card between observations; *build now* costs a new bump-side seam
   (CB-45's contract took three review rounds) **or** core learning an extension's key name
   (which CB-45 forbids) — and note C-A4's third option (per-occurrence capture, §5 bullet 4)
   which has neither cost. See C-A4.

---

## FATAL-7: CONCEDE

**Evidence.** `provenance.py:149-164` — the module's ONE answer constructor returns exactly two
keys:

```python
def _verdict(status: str, reason: str) -> dict[str, Any]:
    return {"file_status": status, "reason": _displayable(reason)}
```

and `:550` — `return _verdict("renamed", f"{file_path} renamed to {new_path}")`. The destination
path exists only inside an English sentence, and `_displayable` (`provenance.py:98-119`) runs
`encode("utf-8","surrogateescape").decode("utf-8","replace")` on it, so a non-UTF-8 rename target
is irrecoverably U+FFFD-mangled **by design**. `check_findings` propagates only `reason`
(`provenance.py:664-674`). Recovering `new_path` means regex-parsing a diagnostic — the
name-matching heuristic CB-57 explicitly refused. And there is nowhere to put the result: `file`
is declared IMMUTABLE with a reason on both entities (CB-21/BT-4), which §0 lists as out of scope.

Corroborated independently by C-R5.

**Fix.** Either (a) drop «переименование файла» from A's «Переживает» cell and say plainly that
after a rename the anchor's `file` still names the old path, and the *resolver* reports
`unknown(renamed_elsewhere)` — honest degradation in the house style; or (b) make the rename
destination a structured field. Option (b) is a real unit and must appear in §9 as such:
`provenance` grows `new_path` in the `renamed` verdict (an additive key — `_parse_rename_records`,
`provenance.py:167-175`, already yields `(old, new)` PAIRS, so the data exists inside the module
and only the response contract hides it) plus a public path-resolution API for SERIOUS-1. Do not
leave the cell asserting a capability no API can deliver.

---

## FATAL-8: CONCEDE

**Evidence.** `batch_add_findings` (`findings.py:1792-1871`) opens ONE `db.txn` and calls
`_add_one` per member with **no `annotate` argument** — verified by grep: `annotate` occurs at
`findings.py:891` (`_add_one` signature), `:1044` (the firing predicate), `:1206/:1211/:1279`
(`add_finding`), `:1392/:1501/:1506` (`import_findings`, which passes `annotate=False`), and
**nowhere inside `batch_add_findings`**. So it defaults to `True` and resolvers run per member
inside the held write lock. CLAUDE.md states this is deliberate.

CB-45's own measured numbers (`CB-45-similarity-seam.md:2107`): «write-lock cost measured at
**23.7 ms/add** (tolerable) vs **~2.4 s/100-batch** (the real hazard)». That was a pure-CPU
resolver. Adding cold-cache file I/O per row is a different order — and `libcheck`, which is the
writer that produced 206 of the list-typed `lines` values, files through `batch_add`
(`SKILL.md:131`). So the batch path is not a corner case; it is the anchor's primary producer.

Corroborated independently by C-R3, and C-M8 adds the missing requirement (per-batch caching of
`(resolved path, range, content state)`).

**Fix.** §7 gains a batch paragraph with a NUMBER the unit must produce before landing: cost of a
100-member batch with cold page cache, measured, compared against 2.4 s. And the design adopts
C-A2: capture I/O runs **before** `db.txn` opens (RFC S2's ratified two-phase pattern,
`RFC-identity-graph-2026-08-17.md:202-210`, which exists for this exact reason), with the resolver
receiving the precomputed anchor object and doing pure arithmetic under the lock. State the
residual honestly: two-phase introduces a TOCTOU window between read and commit, which is
acceptable because the anchor is an advisory snapshot resolved on read (Р5), not a gate.

---

## SERIOUS-1: CONCEDE

**Evidence.** `provenance.py:59` `def _repo_root(cwd)` and `:75` `def _resolve_candidate(root,
file_path)` — both private, no public wrapper anywhere (grepped `provenance.py` for
`def resolve_`/`def repo_root`: none). CLAUDE.md's module rule: *"They must NOT import each
other's private functions — only public interfaces."* §1а's «якорный резолвер ПЕРЕИСПОЛЬЗУЕТ
`_resolve_candidate`» therefore assumes an option that does not exist. Corroborated by C-S10,
which adds the sharper form: `similarity.py` — the precedent BT-7 cites — establishes the OPPOSITE
pattern, reading rows only through the public `findings.similarity_candidates`
(`findings.py:2091-2103`).

**Fix.** §9 gains a Т-0: `provenance` exports a public path-resolution API
(`resolve_in_worktree(file, *, root=None) -> (candidate, rel, inside)` or equivalent),
`_resolve_candidate`/`_repo_root` become its implementation, and `file_status` is refactored onto
it so there is one copy, not two (CB-22/CB-57's drift rule). Fold this with FATAL-7's `new_path`:
they are the same unit, and §9 must name it.

---

## SERIOUS-2: PARTIAL

**What holds.** CB-95's stored description (read via `export-csv`) enumerates, in order:
*CONTENT ANCHOR / SYMBOL ANCHOR / GIT-BLAME-DIFF WALK / HYBRID with an explicit
confidence-`reason` field*. §2's A/B/C/D are those four. The methodology claim «таблица порождена
из П1–П9, не перечислена по памяти» is therefore false about the ROWS.

**What does not hold.** It is true about the COLUMNS. «Переживает / Ломается на / Цена реализации
/ Цена владения» are not in CB-95 and are genuinely derived from П1–П9 (П9 drives A's «работает
вне git» cell; П3 drives D's `reason` cell; §1а's parser measurement drives B's «первая парсерная
зависимость»). The attack overstates by treating the whole table as re-tabulation.

**Fix.** Downgrade the claim rather than delete it: «строки — четыре варианта из самой CB-95;
СТОЛБЦЫ порождены из П1–П9. Генерации новых вариантов не было — и это упущение: §2 не
рассматривает ни отдельную ТАБЛИЦУ (SERIOUS-3), ни прямой diff-hunk mapping (SERIOUS-4 / C-S1,
C-A1).» Then add both as rows E and F with the same cost columns.

---

## SERIOUS-3: PARTIAL

**What holds.** A table was never evaluated. Р2 rejects only «колонка». `relations.py:1-23` is the
counter-precedent, written in this repo and quoted verbatim: *"Relations were being recorded in
ad-hoc JSON `meta` keys — 164 distinct key names for roughly five concepts… That substrate cannot
answer 'what is related to CB-123', and it cannot forget: `meta` writes are merge-only… A relation
that must be queryable AND retractable needs a table."* Its final clause is exactly SERIOUS-9's
problem, and a table would dissolve the one-card-many-places deferral in §4.

**What does not hold — and the attack's implied verdict does not follow.** `meta.anchor` is NOT
the substrate relations replaced. That was 164 caller-invented key names for five concepts; this
is ONE server-owned, versioned structure — which is precisely the shape the occurrence ring
already has, and the ring lives in `meta` **by ratified design** (BT-4, CB-43). Р2's stated reason
for `meta` over a column (a version field must not become schema) survives a table just as it
survives a column, so a table's case has to be made on *queryability and retractability*, not on
Р2's argument being wrong.

**Fix.** Add row E to §2's table — «E. Таблица `finding_anchors(finding_id, idx, v, file, line,
end, hash, context_hash, text, captured_at_commit)`» — with the cost columns filled in honestly:
buys N anchors per card (§4's deferral dissolves), buys retraction (SERIOUS-9 dissolves), buys
`WHERE hash = ?` queries; costs a schema + migration + `register_schema` + export/import/restore
round-trip contracts (`findings.py:1435-1439` and `restore_findings`, `:1604`) that `meta` gets
for free. Then Р2 chooses between meta and table **on that comparison**, not against a strawman
«колонка».

---

## SERIOUS-4: CONCEDE

**Evidence.** §2 dismisses C on «стоимость O(история) на каждое чтение» — a number nobody
produced, against the most expensive form of C. The cheap form exists and its data is already
parsed in this tree: `provenance.py:167-175` `_parse_rename_records` returns `(old, new)` PAIRS
from `git diff --name-status -z --diff-filter=R`, and `provenance.py:544-552` walks them. A single
`git diff -U0 --find-renames <captured_at_commit>..HEAD -- <path>` plus hunk-offset remapping is
O(one diff), needs no blame and no history walk. Corroborated by C-S1 and proposed as C-A1.

**Fix.** Split the C row into **C1 (log/blame walk)** — keep the rejection, and say the cost is
*asserted, not measured* — and **C2 (single rename-aware diff + hunk remap)**, evaluated properly.
C2 fills the gap between A and C1: it survives edits to the anchored TEXT (which defeat A) at the
cost of requiring the file to be in git (П9). It composes with A rather than competing: C2 first,
A as the fallback when a hunk makes the mapping ambiguous. That composition belongs in D's
cascade, which currently reads A → B → unknown.

---

## SERIOUS-5: CONCEDE

**Evidence.** `findings.py:1435-1439`:

```python
dropped_keys = (
    _RESERVED_META_KEYS | _ADD_ONLY_RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
)
```

and `resolver_reserved_meta_keys()` includes every registered resolver's `meta_keys` (proved by
execution in FATAL-1: `reserved: ['anchor', 'resolver_errors', 'similar_to']`). Measured: corpus A
carries `meta.anchor` on **16 rows**, holding **card ids** — three distinct values, `CB-1878`,
`CB-1566`, `CB-1895`. After BT-7 lands, importing that export drops all 16 with no error and no
count. The name collision was one line of the sweep §1а already ran.

**Fix.** Two edits. (1) §1а's census gains the `anchor` key with its ACTUAL values (card ids), and
§1а states the consequence: the name `anchor` is **taken** in a live peer tracker for a different
concept. (2) Р2 either renames the key (`loc_anchor`, `anchor_v1`, or `location`) or declares the
collision knowingly with a migration note. My recommendation: **rename**, because the collision is
silent, the drop is uncounted, and the cost of renaming before anything ships is zero. Add it to
§8 as part of q1's package, one clause.

---

## SERIOUS-6: CONCEDE

**Evidence.** §7 says «`silent` при любой ошибке I/O (`resolver_errors` — queryable)». Those are
opposites, and `db.py:448-451` does BOTH:

```python
sys.stderr.write(f"[pre-add resolver '{resolver.name}' failed] {e}\n")
errors.append({"resolver": resolver.name, "error": str(e)[:500], "at": observed_at})
```

Measured population in corpus A that would raise on `open()`: 171 directories
(`IsADirectoryError`) + 201 non-existent relative paths + 2 globs + 7 prose = **381 of 3176 rows**
(my classifier; the attacker said 385 — same order, the difference is the directory/glob
boundary). Each would stamp `meta.resolver_errors` and write a stderr line per add, destroying
`query(meta_key="resolver_errors")` as the signal CB-45 built it to be.

Corroborated by C-M4, which names the same defect as a *missing requirement*: the document says
"not an error" in Р3(i), "counted skip" in §4, and "queryable resolver_errors" in §7 — three
classifications of one event.

**Fix.** §7 replaces the mitigation sentence with a CLOSED classification, and Р3(i) and §4 are
made to agree with it:

> Захват НИКОГДА не поднимает исключение. Всякий неуспех — `return None` плюс инкремент одного
> счётчика `anchor_capture_skipped(reason)` с закрытым словарём причин (`not_a_file`,
> `out_of_repo`, `unreadable`, `too_large`, `binary`, `no_grammar`, `ambiguous_multisite`).
> `resolver_errors` остаётся ровно тем, чем его сделала CB-45 — сигналом о СЛОМАННОМ резолвере,
> а не о нераспознанной подаче.

---

## SERIOUS-7: CONCEDE

**Evidence.** `similarity.py:54,58` — `DEFAULT_THRESHOLD = 0.7`, `MIN_TEXT_LEN = 40`, applied at
`:164`, `:180`, `:246`. CLAUDE.md's reason: *"trigram Jaccard scores 'Bug 1'/'Bug 2' ≈ 0.8 and two
empty strings 1.0."* BT-7 inherits the extension architecture (Р3(i), N-3) and drops its one
calibrated constant. Р6 states no minimum anchor length, no maximum, and no rule for a degenerate
anchor (`)`, a blank line, `pass`).

The indentation half is worse and is corroborated independently by C-S8: «схлопнуть пробелы»
erases leading whitespace, so in Python the identical statement at two nesting depths hashes the
same — and wrapping a block in `if`/`try` (the single most common real edit) becomes invisible to
`hash` and `context_hash` **simultaneously**, since both are built by the same normalizer.

**Fix.** Р6 gains three constants and one carve-out, all named as numbers: `MIN_ANCHOR_CHARS`
(calibrated, not chosen — the unit measures it against the corpus the way CB-45 measured 0.7);
`MAX_ANCHOR_LINES`; and **leading whitespace is normalized to a DEPTH TOKEN, not erased** (`\t`
expanded, then `indent=N` prepended to the normalized line), so a reindent changes the hash —
which is correct: a reindent IS a code change, and `moved` with a new line is a better answer than
a false `current`. Then Р6's «схлопнуть пробелы» applies to INTERIOR runs only.

---

## SERIOUS-8: CONCEDE

**Evidence.** `provenance.py:655-656`:

```python
effective = _effective_commit(f)
cache_key = (f["file"], effective)
```

The cache is keyed by `(file, commit)`. An anchor probe reads the WORKING TREE, whose content is
not a function of `effective` — two findings on one file at one effective commit would share a
cached verdict that is correct for `file_status` and wrong for an anchor.

Second half verified: the MCP tool takes no `project_dir`:

```python
# provenance.py:798-834
def staleness_check(finding_id=None, status=None, category=None, file=None):
    ...
    return check_findings(conn, None, finding_id=..., ...)
```

so `cwd = project_dir or _ambient_cwd()` (`:603`), and `_ambient_cwd` (`:16-34`) documents that it
returns **None** when the directory was deleted — *"a long-lived MCP server outlives the git
worktree it was started in"*. CB-93 settled the coordinate system for git/stat probes reached
through `file_status`; it did not settle it for a new read surface.

**Fix.** Р5 states: the anchor probe is **not cached** by `(file, effective)` — either a separate
cache keyed by `(resolved path, st_mtime_ns, st_size)` or none at all in v1, with the cost
measured; and `anchor_resolve` takes an explicit `project_dir`/`--repo`, defaulting to the
tracker's own root via `db.describe_root()` rather than to `_ambient_cwd()`. Say plainly that
`staleness_check(anchor=True)` inherits `check_findings`' ambient binding and is therefore the
WEAKER surface — that is CB-11/CB-49's «binding you cannot see» lesson, and Р5 should not create a
third answer to «which directory».

---

## SERIOUS-9: PARTIAL

**What holds.** `update_finding` (`findings.py:2012-2013`) is `new_meta.update(meta_update)` —
merge-only, verified. There is no delete. `{"anchor": null}` leaves the key present holding
`null`. `relations.py:5-9` says the same about the substrate generally.

**What does not hold.** The consequence is milder than «a stale `meta.anchor` forever» implies,
because Р5 is a READ-side design: `anchor_resolve` on a card whose code was deleted reports
`lost`, and the stored anchor never produces a wrong answer — it produces an un-resolvable one,
which is what `lost` means. CB-26's shape is *unrepairable*, and Р3(iii) makes the anchor
**rewritable**; only *removable* is missing. The attacker's "only half satisfied" is right;
"permanent" over-reads it.

**Fix.** Р2 defines the tombstone rather than leaving it to be discovered: `meta.anchor = null`
(or `{"v": 1, "retracted": true}`) is a legal stored value meaning *this card has no anchor and
should not be re-captured*, and `anchor_resolve` returns `unknown(retracted)` on it. One sentence,
and it makes `meta`'s merge-only property harmless here. If SERIOUS-3's table option is taken
instead, this dissolves.

---

## SERIOUS-10: PARTIAL — the measurement claim is false, the SHAPE is real

**What holds.** Measured across both corpora: **zero** occurrences of `[0, 0]` and zero of `[0]`
among all 206 all-int lists. §4's framing («порождена из измеренных форм §1а, не придумана») is
therefore false for this branch — it did not come from §1а's measurement.

**What does not hold — and this is a defence the attacker's method could not reach.** The shape is
REAL and greppable in a live writer's source:
`/home/faxik/.claude/skills/arch-health/state/pending-2026-08-17-arch-health.py` calls
`add(..., [0, 0], ...)` at lines 195, 224, 245, 265, 287, 302, and its `add()` writes
`meta["lines"] = lines` **and** `-l f"{lines[0]}-{lines[1]}"` — with `--meta` winning, exactly the
CLI trap §1а itself documents. It is absent from the corpora because the snapshot `corpus.csv` is
dated **2026-08-17 09:57** and those rows post-date it (verified: the 29 arch-health-tagged rows
in the snapshot are all from 2026-08-04 and carry `None`, `[588, 762, 1116]`, `[1101]`).

**Fix.** Correct the provenance of the claim rather than deleting it: §4 says «`[0,0]` — форма,
взятая из ИСХОДНИКА писателя (`arch-health/state/pending-2026-08-17-arch-health.py:195` и др.), а
НЕ из §1а: в снимке корпуса от 2026-08-17 09:57 её ещё нет, потому что тот прогон позже снимка».
This also repairs §4's blanket methodology sentence, which must become «из измеренных форм §1а И
из прочитанных контрактов писателей», with each branch citing which. Note the consequence for
FATAL-2: under the corrected list rule, `[0,0]` yields first element `0`, which is not a line — so
the «не код» case is caught by a `line >= 1` bound (C-M1) rather than by a special case for one
literal.

---

## SERIOUS-11: CONCEDE

**Evidence.** §5 bullet 2: «честно только с `captured_at_commit` из ринга/колонки и **checkout'ом
того коммита**». A checkout inside the live worktree destroys uncommitted work, and the entire
`tools/worktree-*.sh` harness documented in CLAUDE.md exists to make exactly that class of
operation impossible. The non-destructive form is `git show <commit>:<path>`, which needs no
checkout, no lock and no worktree.

**Fix.** Replace «checkout'ом того коммита» with «чтением содержимого через `git show
<commit>:<path>` — БЕЗ checkout'а: checkout в живом дереве уничтожает незакоммиченную работу и
запрещён харнесом». A one-word-class change, but a deferred bullet is how a mechanism gets
implemented as written six weeks later.

---

## SERIOUS-12: CONCEDE

**Evidence.** A new self-registering module needs three registrations, all verified present for
the existing extensions and all absent from §9:

- `db.py:_ensure_modules_loaded()` — imports `bench, blockers, claims, findings, merge,
  milestones, provenance, relations, reqs, similarity, sweep`.
- `server.py:201-214` `SERVER_NAMES` — includes `"similarity": "codesimilarity"`,
  `"relations": "coderelations"`.
- `cli.py:110-113` `--mode` choices — includes `"similarity"`, `"relations"`.

The second half is sharper: **Т-a alone changes three existing verbs' behaviour** — `add` and
`update` begin refusing `meta={"anchor": …}` (proved by execution, FATAL-1) and `import` begins
stripping it (SERIOUS-5) — before `anchor_resolve` exists to justify it. Corroborated by C-M7.

**Fix.** §9 becomes: Т-0 (provenance public path API + `new_path`, per SERIOUS-1/FATAL-7); Т-a
(module + grammar + normalizer + **registration in all three places** + `updatable_keys` + tests);
Т-b (`anchor_resolve` MCP/CLI + `staleness_check(anchor=)` + wire golden regen); Т-c (backfill
dry-run, optional). And Т-a's brief carries an explicit line: «Т-a меняет поведение
`add`/`update`/`import` до появления читателя — это осознанно, и тесты на отказ и на strip пишутся
в Т-a, а не в Т-b».

---

## W-1: CONCEDE

**Evidence.** `findings.py:434-441`:

```python
if isinstance(v, str) and len(v) >= 3 and _is_volatile_meta_key(k)
```

inside `for k, v in meta.items()` — **top level only, `str` values only**. A nested `anchor` dict
is unreachable, so neither the key `anchor` nor the subfield `captured_at_commit` (which does
contain `commit`) can affect the fingerprint. П8 flags a non-trap. Corroborated independently by
C-S9.

The attacker's addendum is correct and worth keeping: `_derive_fingerprint` runs at
`findings.py:937-938`, resolvers at `:1044` — so the captured anchor **cannot** influence its own
row's fingerprint. Р1's guarantee holds for a structural reason the document never gives.

**Fix.** Rewrite П8: «Ловушка нормализации НЕ активна для структурированного якоря — цикл
`_normalize_for_fingerprint` (`findings.py:434-441`) обходит только ВЕРХНИЙ уровень и только
`str`-значения, так что вложенный dict недостижим. Реальная ловушка — ОБРАТНАЯ: если будущая
версия УПЛОЩИТ якорь (`meta.anchor_commit = "abc…"`), это значение станет вырезаемым и identity
изменится у каждой строки, его несущей. Фиксируется как запрет на уплощение, с тестом.» And add to
Р1: «Гарантия Р1 держится СТРУКТУРНО: `_derive_fingerprint` вызывается на `findings.py:938`,
резолверы — на `:1044`; захваченный якорь физически не может повлиять на отпечаток собственной
строки.»

---

## W-2: PARTIAL

**What holds.** `_repo_root` (`provenance.py:59-73`) returns `None` when git cannot say — no repo,
bare repo, git unusable. Р3(i)'s gate is «файл читаем **от корня worktree** (CB-93)», so with no
root there is no coordinate system and capture cannot fire. A's headline differentiator in §2 —
«работает на любом читаемом файле, включая **вне git**» — is switched off by the recommendation's
own precondition, and A is recommended largely on it.

**What does not hold.** The §2 cell is a statement about the ALGORITHM (content hashing needs no
git), which is true; the gate is Р3(i)'s deployment choice, which is separable. The two are
inconsistent, not both false.

**Fix.** Make the gate say which of the two it means, since they have different scopes: «Захват
работает от РАЗРЕШЁННОГО пути: (a) внутри worktree — путь от корня (CB-93); (b) вне git —
абсолютный путь, если он разрешён §7а. Вариант (b) — ровно то, чем A отличается от B и C, и он
ВКЛЮЧЁН только если владелец ответит да на Вопрос №4 (§7а/CB-91). Если ответ нет, преимущество A
по П9 не реализуется, и это надо сказать в §2, а не оставить в ячейке.»

---

## W-3: CONCEDE

**Evidence.** `CB-45-similarity-seam.md:231` — *"largest live category 92 rows × 1498-char
descriptions = **23.7 ms**/scan"*; `:1369` and `:2107` repeat 23.7. The **34.5 ms** figure is from
`RFC-identity-graph-2026-08-17.md:213` and `:349`, a later re-measurement under the same lock. §7
attributes 34.5 to CB-45.

**Fix.** §7: «образец: CB-45 замерил 23.7 ms/add под тем же локом и принял; RFC identity graph
позже перемерил тот же проход как ≈34.5 ms — и ОБА числа второстепенны рядом с ~2.4 s на батч в
100 членов (`CB-45-similarity-seam.md:2107`), который и есть реальный порог для якоря (FATAL-8).»

---

## W-4: CONCEDE

**Evidence.** `RFC-identity-graph-2026-08-17.md:272-274`: *"**D1** … The review settled the
technical half … Remaining decision is only whether to pay the schema cost now."* And the cost HAS
been paid: `src/codebugs/relations.py` exists with `RELATIONS_SCHEMA` and a live
`finding_relations` table, registered as `"relations": "coderelations"` in `server.py:212` and in
`cli.py`'s `--mode` list, and imported by `_ensure_modules_loaded`. S1 has shipped. §6 was written
against the RFC document, not against the tree.

**Fix.** §6 point (3): «relations-леджер (S1) **уже приземлён** (`src/codebugs/relations.py`,
режим `coderelations`) и не знает о якоре — связь карта↔карта и связь карта↔код разные графы.» And
the closing sentence: «не блокируется нерешёнными **D2/D3**» — D1 is decided.

---

## W-5: CONCEDE

**Evidence.** `provenance.py:149-164` — `_verdict` returns exactly `{"file_status", "reason"}`. The
house-style precedent BT-7 invokes twice has NO confidence field; it has `reason`, a closed token
vocabulary (`no_provenance, no_cwd, empty_path, invalid_path, relative_git_env, out_of_repo,
unreachable_commit, git_error, not_in_commit, stat_error, unsupported_path_kind` — enumerated by
grepping all 20 `_verdict(` call sites). Р5 adds `confidence` with no scale, no derivation and no
vocabulary. Corroborated by C-M9.

**Fix.** Delete `confidence` from Р5's return shape. `status` already carries the distinctions
(`current|moved|lost|ambiguous|unknown`) and `reason` carries the why. If a numeric score turns out
to be needed, it arrives later with a calibration, the way `DEFAULT_THRESHOLD = 0.7` did — «by
measured demand» is this document's own rule and it applies to its own fields.

---

## W-6: CONCEDE

**Evidence.** «построчный seek» is not a thing: you cannot seek to line N without reading the
preceding bytes. So a file-size cap does not bound the anchor read — a large file with a low anchor
line is cheap, but a HIGH line number in a large file costs the full prefix. Real corpus values,
measured: `meta.lines = {'B1': '4189', 'B2': '4265-4274', 'B3': '4279-4281', 'B4': '4246 (missing
guard)'}`, and the maximum line number referenced anywhere in corpus A is **5708**
(`"3330,3374,5653-5708"`).

**Fix.** §7: «Захват читает файл ПОСЛЕДОВАТЕЛЬНО до целевой строки — «построчного seek» не
существует. Поэтому ограничитель — не размер файла, а `max_bytes_read` (прочитанные байты до якоря
+ контекст), и он же покрывает случай «строка 5708» (реальное значение в корпусе).»

---

## W-7: CONCEDE

**Evidence.** `fsio.py:178-196` carries the complete node-kind taxonomy for the WRITE direction
(CB-76): `S_ISDIR` → `IsADirectoryError`; `S_ISFIFO or S_ISCHR` → write in place; `S_ISREG` →
temp+replace; else (block devices, sockets) → `OSError(EINVAL, "unsupported destination type")`.
BT-7 specifies none of the read-direction equivalent, and a `file` pointing at `/dev/stdin` or a
FIFO would block **inside the write lock**.

**Fix.** Folded into FATAL-5's §7а: refuse non-`S_ISREG`, citing `fsio.py:178-196` as the precedent
to mirror rather than re-derive.

---

## W-8: CONCEDE

**Evidence.** The owner's verbatim request (CB-95, read via `export-csv`): *"can we have some more
stable and robust fingerprint of location than just a line in a file? **Ideally, with
auto-resolution of the new line**."* After BT-7 the resolved line is reachable only from a verb an
agent must know to exist. The precedent for surfacing exists and is recent: `findings.py:1145`
`_RESPONSE_ONLY_KEYS = frozenset({"was_new", "dedup_action", "attention"})`, `:1188`
`result["attention"] = list(outcome.attention)` — BT-5's structural, always-present block whose
whole rationale was *"a serious divergence becomes a STRUCTURAL, top-level field instead of
something a reader has to dig out of the body"*.

**Fix.** Р5 gains: `get(finding_id)` carries a resolved `anchor` block ONLY when the caller asks
(`get(..., resolve_anchor=True)`), because Р5's «`get`/`query` НЕ трогают файловую систему» is
correct and must not be weakened by default. But the DEFAULT `get` response carries the STORED
anchor (pure DB, no I/O) so a reader at least sees that one exists and what verb resolves it. State
that split explicitly — it is the difference between meeting the request by construction and
meeting it in practice.

---

## W-9: PARTIAL

**What holds — reproduced exactly.** Against the same two files:

| claim | doc | attacker | my re-measure |
|---|---|---|---|
| `lines` all-int list total | ≥210 | 206 | **206** |
| list lengths 6…20 | 19 | 15 | **15** |
| `lines` value types (A) | — | — | **331 str + 206 list + 2 dict = 539** |
| `lines` `"a-b"` (A) | 180 | 180 | **180** |
| `lines` comma-list (A) | 61 | 61 | **61** |
| `lines` bare-int-string (A) | 43 | 43 | **43** |

So the two head errors the attacker names are real. C-S4 reproduces the same subtotal
independently.

**What does not hold.** The `file:line` / prose split is CLASSIFIER-DEPENDENT and no party
reproduces another. Doc: 41/6 (A) and 33/1 (B). Attacker: 40/7 and 32/2. Mine: **38/9** and
**31/3**. The invariant is the TOTAL of the irregular residue — **47 in A, 34 in B** — which all
three agree on. Codex flagged exactly this in its confidence note: *"I could not reproduce … the
undocumented classifier that split 47 irregular `lines` strings into exactly '41 file:line + 6
prose'."* So the attacker's table is right about the two head numbers and is itself unreproducible
on the two tail ones.

**Fix.** Correct 210→206 and 19→15 in §1а. For the split, do NOT publish a number without a
classifier: §1а states «нерегулярный остаток: 47 (autosorter) / 34 (codebugs) строк, разбиение на
«file:line» и «проза» зависит от классификатора и потому НЕ приводится как число — приводится
регексп, которым оно получено». That is the honest form, and it is also what makes §4's grammar
auditable.

---

## W-10: PARTIAL — right conclusion, and neither the doc's number nor the attacker's reproduces

**Measured** (this tracker, 128 rows; the 32 string `meta.lines` values carrying ≥1
`NAME.ext:DIGITS` token):

```
rows with >=1 file:N token: 32   multi-FILE: 12   multi-SITE: 30
```

- The **document's** «33 — мультифайловая проза» is wrong: only **12 of 32** cite ≥2 distinct
  filenames. Corroborated independently by C-S5, which caught the same overstatement.
- The **attacker's** «21 of 32» reproduces on neither axis.
- The right number is **30 of 32 are multi-SITE** (≥2 line references, same file or not) — which
  makes the attacker's *conclusion* stronger than his number: "one primary anchor" answers 2 of 32
  dogfood cards completely, not ⅔ of them partially.

Plus the 18 `sites` rows FATAL-4 found, which are multi-site by construction (the key is plural).

**Fix.** §4's «Следствие измерения» paragraph is rewritten around the measured numbers: «30 из 32
dogfood-строк с `file:N` токеном называют ≥2 МЕСТА (12 из них — ≥2 разных ФАЙЛА), плюс 18 строк под
ключом `sites`. Один первичный якорь отвечает полностью на 2 карты из 32.» Then the deferral of
`meta.anchors: [...]` is re-argued against THAT, not against a strawman — and the trigger stops
being «доля карт, у которых ПЕРВЫЙ якорь `lost`» (which cannot fire in the right direction) and
becomes the count of dropped sites, which capture already knows at capture time and can stamp as
`anchor_sites_dropped`. Note that SERIOUS-3's table option dissolves this question entirely, which
is a second reason to evaluate it.

---

## N-1: CONCEDE

**Evidence.** `provenance.py:149-164` — the returned key is `file_status`, not `status`; and of the
20 `_verdict(` call sites, the `renamed` (`:550`) and `deleted` (`:552`) reasons are free prose
(`f"{file_path} renamed to {new_path}"`), not members of the token vocabulary §1а lists. П3
conflates the two.

**Fix.** П3: «`provenance.file_status` возвращает `{file_status, reason}`; `reason` — ЗАКРЫТЫЙ
словарь токенов для `unknown` и СВОБОДНАЯ проза для `renamed`/`deleted`. Якорь наследует первую
половину (закрытый словарь) и НЕ наследует вторую — FATAL-7 показывает, чем проза в `reason` уже
стоила.»

---

## N-2: CONCEDE

**Evidence.** True of the literal key in `src/`: `findings.py:1352-1354` (CSV import) and
`:3314-3315` (CLI `add -l`). But `findings.py:2954` advertises it on the public MCP tool — *"meta:
Optional JSON metadata (**lines**, module, rule_code, etc.)"* — so every MCP client is a writer by
invitation. That is exactly how corpus A acquired 9 grammars.

**Fix.** П1: «в `src/` ровно два писателя ЛИТЕРАЛЬНОГО ключа; но docstring MCP-инструмента `add`
(`findings.py:2954`) прямо приглашает клиентов писать `lines` в `meta`, поэтому писателей системно
— сколько клиентов. Это и объясняет 9 грамматик в §1а.»

---

## N-3: CONCEDE

**Evidence.** Verified: `grep -n "SELECT\|INSERT\|UPDATE \|execute(" src/codebugs/similarity.py`
returns **nothing** — the zero-SQL claim is literally true, and row access goes through
`findings.similarity_candidates` (`findings.py:2091-2103`). `anchor.py` would match on SQL and
diverge on what the zero-SQL argument was protecting: `similarity` is pure CPU over rows the core
handed it; `anchor.py` performs unbounded external I/O inside the caller's write transaction. The
precedent transfers the letter, not the reason.

**Fix.** Р3(i): «`anchor.py` — второе самореrистрирующееся расширение с нулём SQL, НО прецедент
`similarity.py` переносится только буквой: тот резолвер — чистый CPU над строками, которые ядро
само ему передало, а этот выполняет ВНЕШНИЙ ввод-вывод внутри чужой транзакции. Именно поэтому §7а
(границы) и C-A2 (двухфазный захват вне лока) — не украшение, а условие переносимости прецедента.»

---

## N-4: DEFEND — no action; the confirmations are correct and I re-verified them

**Evidence.** Every premise the attacker credits reproduces, and I checked them independently
rather than accepting the credit: П1's zero readers of `meta.lines` (grep: two writers,
`findings.py:1352`, `:3315`; no reader); П6's reserved sets verbatim (`findings.py:235`, `:307`);
П7's auto-capture (`findings.py:2953`, `:3015`); П8's `_VOLATILE_KEY_TOKENS` tuple verbatim
(`findings.py:245`); the `--meta`-beats-`-l` trap reproduced exactly (`findings.py:3313-3317` —
`meta["lines"] = args.lines` then `meta.update(json.loads(args.meta))`) and correctly flagged as a
CB-15-class card; resolvers run under the write lock (`findings.py:1265` `db.txn` → `:1044`);
pre-add resolvers do NOT fire on a bump (the live and reopen branches return at
`findings.py:951-1000`, well before `:1044`); no `import ast` in `src/`; no parser dependency in
`pyproject.toml`; the CB-95 verbatim quote is exact.

**Defence.** Nothing to concede. Recording it because a review whose every item is a concession has
not been read carefully — these premises are the part of §1 that survived, and they are the part
the redraft should NOT re-derive.

---

# Codex (Sol)

## C-R1: CONCEDE — corroborates FATAL-1, and states the nuance more precisely

**Evidence.** Same execution as FATAL-1. Codex's added precision is the correct one and the Opus
attacker did not state it this cleanly: *"`updatable_keys=("anchor",)` permits later updates but
cannot make 'caller-supplied wins on add' true."* Verified in the second run — `update` succeeded,
`add` still refused. So the fix is not a choice between Р3(iii) and Р4; it is that Р4's second
clause is unachievable through this seam at all.

**Fix.** As FATAL-1, plus one line in Р4 spelling out the asymmetry: «`updatable_keys` даёт
Р3(iii), но НЕ даёт «caller-supplied побеждает на add» — на add ключ зарезервирован безусловно.
Это ограничение шва, а не выбор дизайна.»

---

## C-R2: CONCEDE — the trust-boundary framing is the correct one and outranks FATAL-5's

**Evidence.** CB-91 (read via `export-csv`, status **open**): *"THE CAPABILITY, AND WHY IT IS A
DECISION AND NOT A BUGFIX. Answering means running `git -C <directory taken from tracker card
data>`, which is a **new trust boundary** (the tracker shells into a directory named by a card) …
**Blocked on: a decision about the trust boundary and the response contract. Not blocked on
code.**"* CB-89 (status **fixed**) ratified the refusal explicitly. BT-7 §7 authorizes reading any
absolute path and storing its text, and presents it as inherited scope («Говорится прямо, не
прячется») rather than as CB-91's undecided decision.

**Fix.** §7's cross-repo bullet is moved into the new §7а and re-labelled: «Это НЕ унаследованный
скоуп. CB-89 ратифицировала ОТКАЗ; CB-91 оставила разрешение открытым как решение о границе доверия
и явно не заблокирована кодом. BT-7 это решение НЕ принимает — оно становится Вопросом владельцу
№4, а до ответа захват содержится `commonpath`-проверкой, как `file_status`.» This is strictly
better than FATAL-5's "restore the check" framing, because it routes the question to the owner
instead of quietly choosing for him.

---

## C-R3: CONCEDE — corroborates FATAL-8

**Evidence.** As FATAL-8. Codex additionally names the re-read amplification: the same large/NFS
file re-read once per member. Verified structurally — `_add_one` is called per member inside one
`db.txn` (`findings.py:1852-1870`) and nothing between members caches anything.

**Fix.** As FATAL-8, with C-M8's per-batch caching requirement folded in.

---

## C-R4: CONCEDE — corroborates FATAL-6

**Evidence.** As FATAL-6. Codex adds the mechanical reason the promise cannot be kept in v1:
«Dedup bump/reopen returns before resolvers run» — verified, `findings.py:951-1000` returns on both
branches, `:1044` is the resolver call.

**Fix.** As FATAL-6.

---

## C-R5: CONCEDE — corroborates FATAL-7

**Evidence.** As FATAL-7. Codex's framing — *"An anchor resolver cannot open the renamed file
without parsing a human diagnostic or changing provenance's response contract"* — states the two
options exhaustively, which is what §9 needs.

**Fix.** As FATAL-7 option (b), and it becomes part of Т-0.

---

## C-R6: CONCEDE

**Evidence.** `findings.get_stats` (read in full) groups by one of
`("severity","category","status","file","source")` — persisted columns only, via
`SELECT {group_by} as grp, severity, COUNT(*) … FROM findings GROUP BY grp, severity`. There is no
counter table and no event log. §4 says the skip reason «НЕ пишется в meta … но считается:
`anchor_capture_skipped` — метрика в `stats`» — a transient resolver skip that is neither stamped
nor persisted cannot be aggregated by anything, so the demand trigger §5 depends on cannot be
computed. And the trigger is the whole justification for deferring B.

**Fix.** Pick one and say it in §4: (a) stamp a compact `meta.anchor_skipped: "<reason>"` on the
row — cheap, queryable via `query(meta_key=…)`, and the objection «это не ошибка подачи» is
answered by the key NAME, not by absence; or (b) the anchor module owns one counters table and
`register_schema`s it, which contradicts «нулём SQL» and must then be said out loud; or (c) drop
the metric and change §5's trigger to something computable from stored anchors alone (e.g. the
`lost` rate reported by `anchor_resolve` over the live corpus, which needs no capture-time
counter). **(c) is the recommendation** — it needs no new mechanism and it is what §5's own first
bullet already half-says.

---

## C-R7: CONCEDE

**Evidence.** `RFC-identity-graph-2026-08-17.md:281-282`: *"**D5** Fingerprint immutability vs
category renames (CB-61's conflict with `findings.py:694`): **out of scope**; S1/S2 do not touch
stored fingerprints."* D5 does not establish an immutability boundary — it declines to decide one.
П4 («RFC identity graph, D5: граница иммутабельности fingerprint») and §6 point (1) («Р1
подтверждает границу иммутабельности fingerprint») both mischaracterize it.

Codex's second half is the sharper one: Р1 says the anchor is not an input «и не в `auto:v2`»,
which pre-decides a boundary the RFC deliberately left open.

**Fix.** П4: «RFC identity graph, D5 — вопрос иммутабельности fingerprint объявлен ВНЕ СКОУПА, а не
решён.» Р1: scope the claim to what BT-7 can actually decide — «★ Якорь НЕ входит в `auto:v1`. Про
`auto:v2` BT-7 НЕ высказывается: версия отпечатка — отдельный переговорный контракт (CB-43 п.6), и
предрешать его здесь значит закрывать D5, который RFC оставил открытым.» §6 point (1) becomes «Р1
не трогает D5; пересечение — только в том, что якорь живёт по ту сторону текущей границы
`auto:v1`.»

---

## C-R8: CONCEDE — corroborates FATAL-2

**Evidence.** As FATAL-2. Codex's worked example is the cleaner one: `[10,100]`, meaning two
reported lines, becomes a 91-line anchor — «This changes meaning, expands lock-held I/O, and hashes
unrelated code», i.e. it is simultaneously a correctness bug and a cost bug.

**Fix.** As FATAL-2.

---

## C-R9: CONCEDE

**Evidence.** `check_findings` (`provenance.py:584-677`) returns
`{"findings": [ {finding_id, file, file_status, reason, reported_at_commit, checked_commit,
current_head}, … ], "total": N}` — a BATCH whose entries already own `reason` and `file_status`. Р5
says «расширение `staleness_check(..., anchor=True)`» and gives a return shape
`{status, line, reason, confidence}` without saying whether those keys nest, replace, or collide
with the existing `reason`/`file_status`; nor what `anchor_resolve`'s inputs are (id? id list?
file? filter set?), nor how `end` and a renamed path appear.

**Fix.** Р5 gains an explicit shape, and it must NEST rather than merge, because a flat `reason`
would have two producers:

```
staleness_check(..., anchor=True) → каждая запись получает ДОПОЛНИТЕЛЬНЫЙ ключ
  "anchor": {status, line, end, reason, resolved_against: {head, path, mtime_ns}} | null
anchor_resolve(finding_id=None, status=None, category=None, file=None) → тот же батчевый
  контракт, что и check_findings: {"findings": [...], "total": N}
```

`null` means the card carries no stored anchor — distinct from `{status: "lost"}`. And
`resolved_against` answers C-M5.

---

## C-R10: PARTIAL

**What holds.** `update_finding(meta_update=)` (`findings.py:2012-2013`) merely merges caller JSON:
it does not read the file, normalize, hash, compute context, or validate `v`. So Р3(iii) as written
lets a user persist a malformed anchor that `anchor_resolve` must then survive, and requires the
user to reimplement the server's normalizer to produce a valid one.

**What does not hold.** «has no usable write surface» over-reads Р3(iii), which claims only that
manual re-anchoring is *permitted* (as the CB-26 repair path), not that a helper exists. The gap is
a missing verb and a missing validator, not a design hole — and the validator is already demanded
by C-M1.

**Fix.** Two additions, both small. (1) §9 Т-b gains `anchor-recapture <id> [--line N]` (MCP
`anchor_recapture`): server-side re-capture writing through the same code path as the resolver, so
no caller ever hand-builds an anchor. (2) Р2's object becomes a **validated** shape:
`anchor_resolve` and `update_finding` both refuse an anchor failing the C-M1 invariants, and an
unknown `v` degrades to `unknown(unsupported_anchor_version)` rather than raising — Р6 already
promises old versions stay readable, so refusing them would contradict it.

---

## C-R11: CONCEDE

**Evidence.** Verified in two parts. Existing cards have no anchor and backfill is deferred (§5).
Future observations of them bump: `findings.py:951-1000` returns on the live and reopen branches,
before the resolver at `:1044`. So the only cards that can ever acquire an anchor under the
★-recommendations are NEWLY INSERTED ones. Consequently `anchor_resolve` measures `lost`/`moved`
over a population that excludes precisely the long-lived cards §5's triggers are about, and the
deferral of B and of `meta.anchors` rests on a measurement that cannot be taken.

This is the strongest structural finding in either report, because it invalidates the document's
own deferral method rather than one of its clauses.

**Fix.** §5's triggers cannot be «замерено на живом трекере» while capture is insert-only. Either
(a) Т-c (backfill) moves from optional to REQUIRED-before-the-triggers-can-fire, with SERIOUS-11's
`git show` mechanism; or (b) the design adopts C-A4 (per-occurrence capture), so every
re-observation of an old card captures an anchor into the ring and the population fills in
naturally; or (c) §5 states honestly that the triggers are unmeasurable in v1 and the decisions
they gate are deferred *without* a measurement plan. **(b) is the recommendation** — see C-A4.

---

## C-R12: PARTIAL

**What holds.** `_derive_fingerprint` (`findings.py:465-480`) hashes
`[category, file, normalized_description]` — `file` IS an identity input. So moving a defect to
another file, or renaming its file, re-keys identity regardless of the anchor. The anchor
stabilizes lookup INSIDE an existing card; it does not stabilize dedup across the movement.

**What does not hold.** Р1's argument is not about cross-file dedup. It reads: making the anchor a
fifth input would mean «одна и та же находка после перемещения кода получила бы новый fingerprint»
— i.e. it argues against ADDING instability, not that the current fingerprint is stable. That
argument is sound and Codex does not touch it. The defect is that the document never states the
limit, so a reader takes the anchor's motivation to be broader than it is.

**Fix.** Add to §0 («что НЕ решается») one line: «Дедуп ЧЕРЕЗ перемещение файла: `file` — вход
`auto:v1` (`findings.py:465-480`), поэтому переезд находки в другой файл меняет identity независимо
от якоря. Якорь стабилизирует ПОИСК внутри карты, а не узнавание карты через переезд. Это отдельный
вопрос (re-key — переговорный контракт, CB-43 п.6).»

---

## C-S1: CONCEDE — corroborates SERIOUS-4

**Evidence.** As SERIOUS-4. `provenance.py:494-509` already runs
`git diff --name-status -z --diff-filter=R -M` and parses it into pairs; a `-U0` diff of the same
shape gives hunk offsets. Omitting it makes A look uniquely cheap.

**Fix.** As SERIOUS-4 (row C2), and as C-A1 in the cascade.

---

## C-S2: PARTIAL

**What holds.** «A — единственный вариант, честно работающий на ВСЕЙ популяции `file`» is not true
as a claim about *anchoring*. Measured on corpus A (`file` column, root `/home/faxik/w/autosorter`):
2787 relative-exists, **201 relative-not-exists, 171 directories, 2 globs, 7 prose**, 8 absolute.
For 381 of 3176 rows A returns `unknown` — an honest refusal, not an anchor.

**What does not hold.** The document does not actually claim otherwise: the same bullet says «для
прозы/каталога отвечает `unknown(not_a_file)` — тот же деградационный стиль». So the defect is the
headline word «работающий», not the substance. Also note my classification differs from §1а's (§1а
says 161 directories / 21 globs; I measure 171 / 2) — a second classifier-dependence, same shape as
W-9.

**Fix.** Reword the bullet: «A — единственный вариант, который на ВСЕЙ популяции `file` даёт ЧЕСТНЫЙ
ОТВЕТ (якорь либо `unknown(<reason>)`), а не отказ инструмента. ЗАЯКОРИТЬ он может только обычные
читаемые файлы: измерено ~2787 из 3176 строк корпуса A; остальные 381 — популяция П9 и деградация.»
And add the same classifier caveat W-9's fix introduces.

---

## C-S3: CONCEDE

**Evidence.** «~200 строк», «самая медленная», «O(история)», «кеш по HEAD», «дёшево по ожиданию»
are all unmeasured, and §7 says so about the last one in the same document («но НЕ ИЗМЕРЕНО»). A
cost table whose own author flags one cell as unmeasured while stating the other four as fact is
asserting a ranking it did not derive.

**Fix.** §2 gains a footnote marking every unmeasured cell with `(оценка)` and states the rule this
repo already applies elsewhere: «оценка — не число; число производит юнит до посадки». Concretely:
A's «~200 строк» → «(оценка)»; C's «самая медленная» → deleted, replaced by SERIOUS-4's C1/C2 split
where C2's cost is «один `git diff -U0` — не измерено»; §7's capture cost stays flagged and gains
the batch number FATAL-8 demands.

---

## C-S4: CONCEDE — corroborates W-9

**Evidence.** Reproduced independently and exactly: 539 = **331 str + 206 list + 2 dict**; lengths
6–20 total **15**, not 19; total lists **206**, not ≥210. All other top-level key counts reproduce.

**Fix.** As W-9.

---

## C-S5: CONCEDE — and it is the number that reproduces

**Evidence.** Measured: of the 32 dogfood `meta.lines` strings carrying a `NAME.ext:DIGITS` token,
only **12** cite ≥2 distinct filenames. Codex's examples check out — several of the 33 name exactly
one file (`blockers.py:522`, `provenance.py:96`). They are heterogeneous filename-qualified
expressions, not uniformly multi-file prose.

**Fix.** As W-10 — one paragraph rewrite covering both this and the multi-SITE number.

---

## C-S6: CONCEDE

**Evidence.** §4 says the grammar is «порождена из измеренных форм §1а, не придумана», and then
selects the FIRST range as primary. Nothing in either corpus establishes ordering as priority — the
corpora establish only that filers write ranges in some order. The measured consequence (W-10) is
that this discards ≥2 sites on 30 of 32 dogfood rows.

**Fix.** §4 states the choice as a CHOICE with its cost, not as a measured form: «ПЕРВЫЙ диапазон
выбран как первичный ПО СОГЛАШЕНИЮ, не по измерению: корпуса устанавливают порядок, но не
приоритет. Цена измерена — 30 из 32 dogfood-строк называют ≥2 места, так что первичный якорь
отвечает полностью на 2 из 32. Число выброшенных мест штампуется как `anchor_sites_dropped` и есть
триггер для `meta.anchors` / таблицы (SERIOUS-3).»

---

## C-S7: CONCEDE

**Evidence.** Read in the document: Р2's shape is `context_hash: str|null` — optional. §7: «поэтому
`context_hash` **обязателен** в захвате и первичен при поиске». Those are two different stored
contracts and two different resolution algorithms (with a null context, the `ambiguous` branch has
no disambiguator at all).

**Fix.** Make Р2 the authority and §7 consistent with it: `context_hash` is **required at capture**
and `null` is legal only for the degenerate case where the anchor sits at a file boundary with
fewer than N lines of context on one side — in which case the stored value is a short-context hash
with a recorded width, not `null`. Then Р2's type becomes
`context_hash: str, context_before: int, context_after: int` and the `null` disappears, which also
answers half of C-M2.

---

## C-S8: CONCEDE — corroborates SERIOUS-7's indentation half

**Evidence.** As SERIOUS-7. The Python-specific point is correct and neither the document nor Р6
addresses it: indentation is semantic, and «схлопнуть пробелы» erases it.

**Fix.** As SERIOUS-7 — leading whitespace becomes a depth token rather than being erased.

---

## C-S9: CONCEDE — corroborates W-1

**Evidence.** As W-1: `findings.py:434-441` inspects only top-level `str`-valued meta, so
`meta.anchor` being a dict makes the whole П8 trap inert.

**Fix.** As W-1 (П8 rewritten around the INVERSE trap: flattening).

---

## C-S10: CONCEDE — corroborates SERIOUS-1, and states the precedent inversion better

**Evidence.** `similarity.py` issues zero SQL (verified by grep, no hits) and reads rows via the
public `findings.similarity_candidates` (`findings.py:2091-2103`). BT-7 proposes reaching into
`provenance._resolve_candidate` (`provenance.py:75`). The cited precedent establishes the opposite
ownership pattern — it is evidence AGAINST the private coupling, not for it.

**Fix.** As SERIOUS-1 (Т-0: public path API in provenance).

---

## C-M1: CONCEDE

**Evidence.** Р2's object is `{v, line, end, hash, context_hash, text, captured_at_commit}` with no
invariants stated anywhere in the document. Nothing says `line >= 1`, nothing forbids `end < line`,
nothing rejects `True` where an int is expected (Python's `bool` is an `int`, and this repo has
been bitten by exactly that class — CB-25's `mock.ANY` trap and CB-82's falsey-wrong-type path are
the same shape), nothing bounds `text`, nothing says what an unknown `v` does, and Р3(iii) lets a
caller write any of it.

**Fix.** Р2 gains an INVARIANTS block, and Т-a's brief makes it a validator both the resolver and
`update_finding` route through: `v ∈ {1}` (unknown → `unknown(unsupported_anchor_version)` on read,
never an exception); `isinstance(line, int) and not isinstance(line, bool) and line >= 1`;
`end is None or (int, not bool, end >= line)`; `end - line + 1 <= MAX_ANCHOR_LINES`;
`hash`/`context_hash` are lowercase hex of the declared digest length;
`len(text) <= MAX_TEXT_BYTES`; `captured_at_commit is None or 40-hex`. A line beyond EOF at resolve
time is `lost`, not an error.

---

## C-M2: CONCEDE

**Evidence.** Р6 fixes the NORMALIZER in `v` but never specifies the HASH. Unspecified: algorithm,
digest length, input encoding, newline handling (`\r\n` vs `\n` — decisive on a mixed-EOL file),
the separator between multiple anchored lines (and this repo already learned that a joined string
is ambiguous — `_derive_fingerprint`'s docstring, `findings.py:468-470`: *"A JSON array, not a
joined string — … any separator is ambiguous"*), the context width N, and whether context includes
the anchor lines. Versioning cannot rescue an undefined v1.

**Fix.** Р6 specifies v1 completely, and reuses the package's own answer for the separator
question: `sha256` over `json.dumps([<normalized lines>], ensure_ascii=False).encode("utf-8")`,
truncated to the same 32 hex chars `_derive_fingerprint` uses; input decoded `errors="replace"`;
`\r\n` and `\r` normalized to `\n` BEFORE hashing; context = N lines before and N after, hashed as
a separate array **excluding** the anchor lines (so a change to the anchor and a change to its
surroundings are distinguishable); N stated as a number.

---

## C-M3: CONCEDE — corroborates FATAL-5 / C-R2

**Evidence.** As FATAL-5: no numeric caps, no timeout, no encoding/binary policy, no symlink
policy, no authorization for absolute files anywhere in the document. Codex's added point about
line-seek matches W-6 independently.

**Fix.** As FATAL-5's §7а, with C-M3's list as its checklist.

---

## C-M4: CONCEDE — corroborates SERIOUS-6

**Evidence.** Three classifications of one event, read from the document: Р3(i) «иначе якоря нет и
это не ошибка»; §4 «считается: `anchor_capture_skipped`»; §7 «`silent` при любой ошибке I/O
(`resolver_errors` — queryable)». They imply three different observable behaviours.

**Fix.** As SERIOUS-6 — one closed classification, applied at all three sites.

---

## C-M5: CONCEDE

**Evidence.** Р5 returns `{status, line, reason, confidence}`. Nothing identifies WHICH snapshot
`line` refers to. `check_findings` already sets the standard here — every record carries
`checked_commit` and `current_head` (`provenance.py:665-673`) precisely so the answer names its own
basis. An anchor resolved against a dirty working tree with no such field is an answer a consumer
cannot cache, compare, or reproduce.

**Fix.** As C-R9's shape: `resolved_against: {head, path, mtime_ns, size}` on every anchor verdict,
plus `end` and the resolved `path` (which differs from the stored `file` after a rename, if FATAL-7
option (b) is taken).

---

## C-M6: CONCEDE

**Evidence.** The asymmetry is real and verified. `import_findings` strips the dynamic resolver
union (`findings.py:1435-1439`, and `resolver_reserved_meta_keys()` proved to include `anchor` by
execution). `restore_findings` (`findings.py:1604+`) is a raw INSERT that *deliberately* preserves
reserved meta verbatim — its docstring: *"a restore is a statement that these rows ARE the tracker,
so it bypasses dedup, the pre-add resolvers and the post-add hooks"*. So a restored old-format or
unknown-`v` anchor lands untouched, and nothing validates it.

**Fix.** Р2/Р4 gain an import/restore paragraph, stated as the deliberate asymmetry it is: «ИМПОРТ
вырезает `anchor` (динамический резольверный ключ, `findings.py:1435-1439`) — чужой якорь указывает
на чужое дерево и был бы уверенной ложью. ВОССТАНОВЛЕНИЕ сохраняет его дословно (`restore_findings`
— сырой INSERT по контракту), поэтому валидация якоря обязана жить на СТОРОНЕ ЧТЕНИЯ:
`anchor_resolve` деградирует в `unknown(unsupported_anchor_version)` / `unknown(malformed_anchor)`,
а не падает. Обе половины — осознанные, и обе называются здесь, потому что молчащая асимметрия —
это CB-51 заново.»

---

## C-M7: CONCEDE — corroborates SERIOUS-12

**Evidence.** As SERIOUS-12: `_ensure_modules_loaded` (`db.py`), `SERVER_NAMES`
(`server.py:201-214`), `--mode` choices (`cli.py:110-113`), plus `register_tool_provider` /
`register_cli_provider`, plus the wire golden regen (`PYTHONPATH=src uv run python
tests/dump_schema.py > tests/golden/mcp_schema.json`).

**Fix.** As SERIOUS-12's §9 rewrite.

---

## C-M8: CONCEDE

**Evidence.** `batch_add_findings` (`findings.py:1852-1870`) loops `_add_one` with no shared state
between members, so N members touching the same file re-read it N times inside one lock. Nothing in
BT-7 requires a per-batch cache of `(resolved path, range, content state)`. Distinct from
FATAL-8/C-R3: those say the I/O is in the wrong place, this says it is also duplicated.

**Fix.** Folded into C-A2's two-phase design: the pre-lock capture phase reads each distinct
resolved path ONCE per batch and hands the resolver a precomputed per-member anchor. That makes the
requirement structural rather than a discipline the resolver has to remember — which is CB-41's
lesson («point-of-use discipline is the wrong enforcement layer») applied here.

---

## C-M9: CONCEDE — corroborates W-5

**Evidence.** As W-5: `_verdict` has no confidence field, and Р5 defines no formula, calibration,
range or interpretation. Codex's framing is the right one: *"false precision over statuses already
expressing ambiguity"*.

**Fix.** As W-5 — delete `confidence`.

---

## C-A1: CONCEDE (adopt)

**Evidence.** The building blocks exist: `provenance.py:494-509` runs the rename-aware diff and
`:167-175` parses it into `(old, new)` pairs. A `git diff -U0 --find-renames <captured>..HEAD --
<path>` gives hunk offsets from which a stored `(start, end)` remaps arithmetically, with no
content search at all — and it survives edits to the anchored TEXT, which is exactly where A fails.

**Fix.** D's cascade is re-ordered and the reason stated: «C2 (один rename-aware diff → пересчёт
смещений) → A (контентный поиск, когда hunk делает пересчёт неоднозначным ИЛИ файл вне git) →
`unknown(reason)`. B — по замеренному спросу.» C2 first because it is exact when it applies; A
second because it is universal. This also gives Р5 a natural `reason` for the `ambiguous` branch
(«hunk overlap»), which it currently lacks.

---

## C-A2: CONCEDE (adopt) — this is the single highest-value change in either report

**Evidence.** The precedent is ratified and in this repo's own plans:
`RFC-identity-graph-2026-08-17.md:202-210` — *"**The provider call happens OUTSIDE the write lock**
(mandatory fix 1 — corroborated FATAL). Two-phase: at `add` / `batch_add` entry, **before**
`db.txn` opens … the pre-add resolver receives the precomputed query vector in the observation dict
and performs pure arithmetic plus one read under the lock … A batch embeds all members in **one**
provider call, then opens its one DB transaction. (The resolver cannot run outside a transaction —
`db.py:417-423` raises — which is exactly why the I/O must complete before the transaction
begins.)"* Verified: `run_pre_add_resolvers` does raise outside a transaction (`db.py:417-423`).

**Fix.** §7 is restructured around it: «Захват НЕ читает файловую систему под локом. Двухфазно
(прецедент RFC S2, `RFC-identity-graph-2026-08-17.md:202-210`): `add`/`batch_add` до открытия
`db.txn` читают и хешируют файлы (батч — каждый различный путь ОДИН раз, C-M8), кладут готовый
объект якоря в observation, а резолвер под локом делает только валидацию и штамп. Остаточный риск
назван: между чтением и коммитом файл может измениться (TOCTOU) — приемлемо, потому что якорь по Р5
разрешается на ЧТЕНИИ и не является гейтом.» This dissolves FATAL-8, C-R3, C-M8, and most of §7's
unmeasured-cost problem in one move.

---

## C-A3: PARTIAL

**What holds.** The alternative is stronger than the document's rejection allows, and the reason is
FATAL-2: §4 rejects structured `line=`/`end=` arguments because «захват из существующих форм стоит
клиентам ноль» — but that is only true if the existing forms can be read *correctly*, and
FATAL-2/C-R8 show the dominant form cannot. A grammar that misinterprets 76 rows costs clients more
than an argument would.

**What does not hold.** Codex presents this as absent; it is not. §4's final paragraph considers it
explicitly and defers it with a stated reason («четыре живых писателя эмитят четыре формы `lines`,
и менять их контракт ради якоря — цена на стороне клиентов, которую владелец не просил»). The
framing as a *missing* alternative is wrong.

**Fix.** Keep the deferral but re-argue it against the corrected grammar, and make the fallback
explicit: «Структурированные `line=`/`end=` на `add` — ОТЛОЖЕНО, но не по причине «захват бесплатен
для клиентов»: после FATAL-2 захват из `list[int]` даёт первую строку и теряет остальные, т.е. цена
уже платится, просто скрыто. Причина отсрочки — только та, что владелец не просил менять контракт
клиентов. Когда `anchor_sites_dropped` покажет число, аргумент пересматривается.» Also note the CLI
trap §4 already flags (`--meta` beats `-l`) becomes a correctness issue the moment a structured
argument exists, so the CB-15-class card should be filed now, not later.

---

## C-A4: PARTIAL — and this is the alternative that dissolves FATAL-6/C-R11

**What holds, and it is important.** Per-occurrence capture is the option that has none of Р3(ii)'s
costs. The ring already carries `file` AND `meta` per observation (`_occurrence_entry`,
`findings.py:483-525` — `{at, severity, category, file, description, source, tags,
reported_at_commit, reported_at_ref}` plus `meta` when present), and `_effective_commit`
(`provenance.py:555-581`) is the ratified precedent for *"newest usable entry wins"* on read. So:
capture an anchor for every observation outside the lock (C-A2), store it in the occurrence entry,
resolve the newest usable one on read. That needs **no** bump-side resolver seam, **no** core
knowledge of an extension's key name, and **no** exception to BT-4's «top-level meta = авторское
состояние» — the three costs Р3(ii) names as the reason to defer. It also fixes C-R11: old cards
acquire anchors on their next observation, so §5's triggers become measurable.

**What does not hold.** Codex presents it as an unconsidered alternative. §5's fourth bullet DOES
name it («Якорь в ринге per-occurrence») — but defers it on a reason that reads backwards in the
light of Р3: *"ринг уже несёт `file`; `anchor` per-occurrence — только если появится читатель «где
это было на коммите X»"*. That reason evaluates the ring anchor as a HISTORICAL record and misses
that it is also the cheapest route to the CURRENT one.

**Fix.** This must become a real option in §3, not a bullet in §5, and it changes §8's second
question. Concretely: Р3 gains a variant «(ii-alt) якорь захватывается на КАЖДОМ наблюдении и
кладётся в `meta.occurrences[*].anchor`; чтение берёт новейший пригодный (паттерн
`_effective_commit`, `provenance.py:555-581`). Стоит: ноль новых швов, ноль исключений из BT-4,
ноль знания ядра о ключе расширения — то есть все три цены, ради которых Р3(ii) откладывается. Не
стоит: место в ринге (ринг ограничен) и то, что `meta.anchor` верхнего уровня тогда либо не нужен,
либо становится проекцией.» And §8 q2 is re-put to the owner as a **three-way** choice (defer /
bump-seam / per-occurrence), not a yes-no on deferral. Per this repo's rule that a question must
carry the cost of each option, that is the only form q2 can honestly take.

---

## Corroborated pairs

Both attackers found these independently. Each pair is a defect I could not defend on either
reading, and the corroboration is itself evidence that the document's own end-to-end pass (which §3
claims found the Р3(ii) cost) did not run over §4 or §1а.

| # | Opus | Codex | The shared defect |
|---|---|---|---|
| 1 | **FATAL-1** | **C-R1** | Registering the capture as a pre-add resolver reserves `anchor`, so Р4's "caller-supplied wins on add" is impossible. Proven by execution, not argument. |
| 2 | **FATAL-2** | **C-R8** | §4 reads a length-2 int list as `[min,max]`, contradicting `libcheck/SKILL.md:135`, the writer that produced 206 of 206 lists. |
| 3 | **FATAL-6** | **C-R4** | П3 + §8 q1 promise newest-observation inheritance; Р3(ii)★ recommends against it; q2 asks to defer what q1 ratifies. |
| 4 | **FATAL-7** | **C-R5** | A's "survives rename" cell has no data path: the destination lives only in `_verdict`'s prose `reason`. |
| 5 | **FATAL-8** | **C-R3** | `batch_add_findings` has no `annotate`, so capture I/O runs N times inside one `BEGIN IMMEDIATE`. (C-M8 extends it: also N re-reads of one file.) |
| 6 | **FATAL-5** | **C-R2** | Capture reads arbitrary absolute paths and stores source text; the containment boundary is CB-91's *open* trust decision, not inherited scope. |
| 7 | **SERIOUS-1** | **C-S10** | `_resolve_candidate`/`_repo_root` are private, and the cited `similarity.py` precedent establishes the opposite ownership pattern. |
| 8 | **SERIOUS-4** | **C-S1 / C-A1** | Option C is dismissed on an unmeasured O(history) strawman; the cheap single-diff hunk-remap variant is never evaluated, though its parser already exists. |
| 9 | **SERIOUS-6** | **C-M4** | "silent" and "queryable `resolver_errors`" are opposites; `run_pre_add_resolvers` does both. Three incompatible failure classifications across Р3(i)/§4/§7. |
| 10 | **SERIOUS-7** | **C-S8** | The normalizer drops `MIN_TEXT_LEN`'s calibrated lesson and erases indentation, which is semantic in Python. |
| 11 | **SERIOUS-12** | **C-M7** | §9 omits the three registration sites every self-registering module needs. |
| 12 | **W-1** | **C-S9** | П8's "normalization trap" is inert — the fingerprint loop reads top-level `str` values only, so a nested `anchor` dict is unreachable. |
| 13 | **W-5** | **C-M9** | `confidence` is in Р5's return shape and defined nowhere; the house-style precedent deliberately has no such field. |
| 14 | **W-9** | **C-S4** | §1а's list totals do not reproduce: 206 not ≥210, 15 not 19. (Both also independently failed to reproduce the doc's `file:line`/prose split — so did I.) |
| 15 | **W-10** | **C-S5** | The multi-location claim is measured wrong — in *opposite* directions: the doc overstates multi-FILE (12 of 32, C-S5 right) and understates multi-SITE (30 of 32, W-10's conclusion right, its number wrong). |
| 16 | **SERIOUS-5** | **C-M6** | The resolver-key union is stripped on import; the `anchor` name is already taken (16 rows, card ids) and restore preserves it verbatim — three interacting contracts, none declared. |

Sixteen of sixty-nine findings were found twice by different models. Every one of them is in §1а,
§4, Р3 or Р4 — the four places the document claims to be measured rather than reasoned.

---

## Tally

| | CONCEDE | PARTIAL | DEFEND | total |
|---|---|---|---|---|
| **Opus** (FATAL-1..8, SERIOUS-1..12, W-1..10, N-1..4) | 25 | 8 | 1 | **34** |
| **Codex/Sol** (C-R1..12, C-S1..10, C-M1..9, C-A1..4) | 30 | 5 | 0 | **35** |
| **Total** | **55** | **13** | **1** | **69** |

**Opus PARTIAL (8):** FATAL-5, SERIOUS-2, SERIOUS-3, SERIOUS-9, SERIOUS-10, W-2, W-9, W-10.
**Opus DEFEND (1):** N-4 (confirmations, no action).
**Codex PARTIAL (5):** C-R10, C-R12, C-S2, C-A3, C-A4.

---

## Defender's closing: what must change before this reaches the owner

Structure, not detail. Four things are load-bearing; everything else is a fix list.

1. **Р3(i)+Р4 must be rewritten together** (FATAL-1 / C-R1). Not because the attack was clever, but
   because I ran it and the add is refused. A ratification line that describes behaviour the code
   refuses is the one defect this repo's CLAUDE.md is almost entirely about.
2. **§8's question 1 cannot be asked in its current form** (FATAL-6 / C-R4), and question 2 must
   become a three-way choice once C-A4 is on the table. The owner's standing rule is that a
   question carries the cost of each option; today q1 carries a clause the document recommends
   against and q2 offers two options where there are three.
3. **§4 must be re-derived from a shape-based sweep** (FATAL-2, -3, -4 / C-R8, C-S5, C-S6). The
   current grammar misreads its dominant writer, gates a branch that matches 2 rows in 128, and is
   blind to the second-most-common locational key in both corpora. Its own `anchor_capture_skipped`
   metric — which §5's deferrals depend on — is therefore measuring the wrong population, and C-R6
   shows it cannot be computed at all as specified.
4. **The read boundary is a decision, not inherited scope** (FATAL-5 / C-R2). CB-91 is open and
   says so in its own text. BT-7 should route it to the owner as question 4, not settle it in a
   risks bullet.

And one thing worth adopting that neither attacker framed as a single move: **C-A2 + C-A4
together** — capture outside the lock, into the occurrence ring — dissolves FATAL-8, C-R3, C-M8,
C-R11 and most of Р3(ii)'s cost argument at once, and it does so using two patterns this repo has
already ratified (RFC S2's two-phase; `_effective_commit`'s newest-usable-entry). That is the
redraft's spine.
