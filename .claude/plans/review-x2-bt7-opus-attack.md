# BT-7 «Локационный якорь» — hostile adversarial review (Opus attacker)

Target: `.claude/plans/BT-7-location-anchor.md` (v1, DIR-2, 2026-08-22)
Method: every named symbol grepped in `src/`; every corpus number re-measured from
`codebugs export-csv` (128 rows, read-only) and `/home/faxik/.cache/codebugs-identity/corpus.csv`
(3176 rows); every CLAUDE.md contract read at its source line.

**Verdict: FAIL-REVISE.** The direction (content anchor, resolve-on-read, not an identity input)
is defensible. The document that carries it is not: its central mechanism (Р3(i)+Р4) is
internally impossible against the seam it names, its capture grammar (§4) is built on a
misread of its own dominant writer and on a branch that measures to 0–2 rows, its premise
sweep (§1а) missed a locational key in **both** corpora, and it introduces an unbounded
arbitrary-file-read into the MCP surface while explicitly deleting the containment check this
repo added under review.

---

## FATAL

**FATAL-1: Р3(i) and Р4 are mutually exclusive. Registering the capture as a pre-add resolver
makes `anchor` a RESERVED key, so a caller cannot supply it at all — and if it could, the
RESOLVER would win, not the caller.** (`src/codebugs/db.py:280-300`, `src/codebugs/findings.py:276-288`, `src/codebugs/findings.py:1044-1069`)

Р3(i) says capture happens «через `register_pre_add_resolver` (annotate-only seam, CB-45)».
Р4 says «`anchor` — НЕ в `_RESERVED_META_KEYS` … резолвер не перезаписывает якорь, переданный
вызывающим явно (caller-supplied побеждает, как caller-supplied `fingerprint`)».

Both halves of Р4 are refuted by the code:

1. `register_pre_add_resolver`'s own docstring (`db.py:292-294`): *"`meta_keys` declares the
   ONLY meta keys this resolver's annotation may write; **findings reserves the union against
   caller-supplied meta**."* And `findings.py:278-283`:
   ```python
   reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
   if updating:
       reserved -= db.resolver_updatable_meta_keys()
   else:
       reserved |= _ADD_ONLY_RESERVED_META_KEYS
   ```
   So the moment `anchor.py` calls `register_pre_add_resolver(..., meta_keys=("anchor",))`,
   `add(meta={"anchor": …})` raises `ValueError: meta keys ['anchor'] are reserved…`.
   Р4 is technically true about the literal frozenset at `findings.py:235` and **false about the
   behaviour**, which is what a ratification line has to be right about. П6 even names the
   registry route («плюс расширения (`similar_to`, `resolver_errors`) через реестр резолверов»)
   and then Р4 draws the opposite conclusion three lines later.
2. Registering with `meta_keys=()` is not an escape: `_validate_resolver_outcome`
   (`db.py:365-372`) computes `bad = (set(outcome) - resolver.meta_keys) | …` and refuses any
   undeclared key. The resolver could then write nothing.
3. Even granting a caller-supplied anchor, "caller-supplied побеждает" is backwards. The core
   does `meta_final.update(db.run_pre_add_resolvers(...))` (`findings.py:1044-1069`) — a
   `dict.update` where the **resolver patch is the right-hand side**. The resolver overwrites the
   caller. The `fingerprint` analogy Р4 leans on is the opposite shape: there the CORE enforces
   it (`if fingerprint is None: fingerprint = _derive_fingerprint(...)`, `findings.py:937-938`).
   For meta there is no such core rule; the property would have to be re-implemented inside the
   extension and nothing enforces it.

**And Р3(iii) is broken by the same registration.** Manual re-anchoring via
`update_finding(meta_update={"anchor": …})` also hits the reserved union, unless the resolver
declares `updatable_keys=("anchor",)` — which the document never states anywhere. `similarity`
had to do exactly that (`similarity.py:374-379`, with the CB-26 rationale in the comment above
it). BT-7 asserts the outcome and omits the one line that produces it.

---

**FATAL-2: §4 reads a length-2 int list as `[min, max]` — contradicting the documented contract
of the writer that produced most of them.** (`/home/faxik/.claude/skills/libcheck/SKILL.md:135`; measured over 3176 corpus rows)

§4: *«`list[int]` любой длины (≥210 — берётся `[min, max]` если длина 2 и значения упорядочены…»*.
§1а itself names the writer: *«скилл `libcheck` (`{"lines": [line_numbers]}` — JSON-список int
по контракту скилла)»*. The skill's actual line:

```
- `meta`: `{"lines": [line_numbers], "confidence": "high"|"medium"}`
```

`[line_numbers]` is a list of **individual line numbers**, not a range. Measured samples of
length-2 lists in the corpus: `[56, 106]`, `[487, 489]`, `[52, 55, 91, 92]` (len 4 — same writer,
plainly not a range). Reading `[56, 106]` as a range anchors a **51-line block**, hashes it,
and reports it as the finding's location. This is not a degradation to `unknown`; it is a
confident wrong answer, which is the exact failure class CLAUDE.md's provenance rules exist to
forbid ("a question that errored producing a confident verdict", `provenance.py:44-52`).

Re-measured list-length distribution (`lines`, autosorter corpus, all elements `int`, n=603):

| len | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 13 | 16 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rows | 40 | 76 | 42 | 25 | 8 | 2 | 3 | 3 | 1 | 3 | 1 | 1 | 1 |

**Total 206, not «≥210».** So §4's rule set is: 76 rows misread as ranges, 130 rows collapsed to
their first element (silently discarding 2–19 further sites each). Zero of the 206 are handled
correctly by the stated grammar.

---

**FATAL-3: §4's `file:N-M` branch is dead by measurement — 2/31 in codebugs, 0/38 in autosorter.**
(measured; e.g. `meta.lines = "findings.py:142-190,193-232; sweep.py:50,52,…"` on a row whose `file` column is `src/codebugs/findings.py`)

§4: *«`file:N-M (...)` (41 + 33 dogfood — только если `file`-префикс совпадает с колонкой
`file`, иначе пропуск: карта цитирует другой файл)»*.

I measured the leading `path.ext:N` token of every string `meta.lines` against that row's `file`
column:

| corpus | rows with a leading `file:N` token | exact match with `file` column | basename-only match | no match |
|---|---|---|---|---|
| codebugs | 31 | **2** | 29 | 0 |
| autosorter | 38 | **0** | 38 | 0 |

The prose dialect is **basenames** (`findings.py:142`); the `file` column holds repo-relative
paths (`src/codebugs/findings.py`). Under the stated gate the branch captures 2 rows in one
corpus and none in the other — while the document cites "41 + 33" as its justification for
including it. Loosening the gate to basename matching is not a free fix either: `findings.py`
is ambiguous across a tree with multiple same-named files, and the branch's whole stated purpose
is refusing rows that cite a *different* file.

---

**FATAL-4: §1а's locational-key sweep is wrong in BOTH corpora — it misses `sites`/`site`.**
(measured: 18 rows in this tracker, 43 in autosorter)

§1а asserts for codebugs: *«44 с локационным ключом — все под `lines`, ни одного
`line`/`loc`/`symbol`/`function`/`anchor`»*. Measured meta-key census over the same 128 rows:

```
notes 98, found_by 69, lines 44, assignee 41, relates_to 32, verified 21,
related 20, sites 18, branch 16, ...
```

`sites` holds exactly what the anchor is for:
- `"merge.py:263-267 idempotent short-circuit (no expiry check); the acquisition path below it…"`
- `"milestones/triage.py:~32, milestones/capacity.py:~167"`

Autosorter: `sites` 30 + `site` 13 (values `"reorganization/scorer.py:255-262"`,
`['sync_service.py:497-503 picker', 'source_adapter.py:116-153 port adapter']`, and two dicts).
So the real key set is **seven** keys, not the "три соседних ключа" §1а claims, and the two most
unambiguously locational ones are absent from the premise, from §4's grammar, and therefore from
the `anchor_capture_skipped` demand metric §4 promises will make the symbolic-anchor decision
"ЗАМЕРЕН, а не угадан". Those 61 rows will be counted as *has no location* when they have one.

This is verbatim the failure CLAUDE.md names and BT-7 quotes at itself: *"Sweep for the SHAPE,
not for the names — the first pass grepped an enumeration of the filters already known."* The
document reproduced the enumeration bug inside the section whose job was to avoid it.

Also measured: the autosorter `anchor` key (16 rows) does **not** hold locations — its values are
card ids (`"CB-1878"`, `"CB-1566"`). §1а calls it "проза (имя функции / описание)", which is
wrong about what it is. See SERIOUS-5 for the consequence.

---

**FATAL-5: capture turns MCP `add` into an arbitrary-file-read primitive that stores source text
in the tracker, by deliberately deleting the containment check `file_status` enforces.**
(`src/codebugs/provenance.py:334-345`; `BT-7 §7`, `Р2`)

`file` is a free-text argument of a public MCP tool (`findings.py:2924` — *"file: File path
relative to project root"*, but CB-88/CB-89 established absolute cross-repo paths, directories,
globs and prose are all in deliberate use). `provenance.file_status` therefore refuses anything
outside the worktree, explicitly and under review:

```python
candidate = _resolve_candidate(root, file_path)
try:
    inside = os.path.commonpath([root, candidate]) == root
except ValueError:
    inside = False
if not inside:
    return _verdict("unknown", f"out_of_repo (worktree {root})")
```

BT-7 §7 removes that boundary on purpose: *«якорь у карты кросс-репо (абсолютный `file`)
захватывается, если путь читаем; разрешение — тоже по абсолютному пути. Говорится прямо, не
прячется.»* Combined with Р2, which stores **`text: str` — the source line verbatim** — the
result is:

```
add(category=…, file="/home/faxik/.ssh/id_rsa", description=…, meta={"lines": "1-3"})
→ meta.anchor.text = "-----BEGIN OPENSSH PRIVATE KEY-----…"
→ readable via get / query / export-csv, and shipped in every CSV export
```

The document names cross-repo capture as an *honesty* item and never as a security item. It also
never mentions: symlinks (`_resolve_candidate` resolves only the PARENT physically — a reviewed
asymmetry at `provenance.py:85-96` that is safe **only because** the containment check follows
it), `/dev/*` and FIFOs (a blocking read inside the write lock), binary files, or text encoding.
There is no size cap, no line cap, and no `text` length cap in Р2's shape.

Minimum acceptable fix: keep `commonpath` containment, cap `text` length, refuse non-regular
files, and read with `errors="replace"` — none of which the document specifies.

---

**FATAL-6: П3 states as a MUST what Р3 declines, and §8's question 1 asks the owner to ratify the
declined version.** (BT-7 §1 П3; §3 Р3; §8 п.1–2)

- П3: *«Якорь ДОЛЖЕН наследовать обе конвенции — словарь с `reason` и **«новейшее наблюдение»**.»*
- Р3(ii) recommendation: *«★: на первом шаге (ii) НЕ строить»*.
- §8 question 1, the package the owner is asked to answer да/нет to: *«якорь = контентный хеш
  строк в `meta.anchor`, захватывается сервером при подаче по `lines`, **обновляется новейшим
  наблюдением**, разрешается на чтение отдельным вызовом и не влияет на identity»*.
- §8 question 2 then asks the owner to agree to **defer** the clause question 1 just asked him to
  ratify.

A "yes" to question 1 ratifies the thing the document recommends against; a "no" to question 1
rejects the four clauses it does recommend. The premise П3 is never retracted. Per the owner's
own standing rule — *"Before any question, reconstruct it from zero… options with the cost of
each"* — this is a question that cannot be answered correctly, and it is the load-bearing one.

---

**FATAL-7: option A's "переживает переименование файла" cell is not implementable from the
current API — the new path exists only inside a lossy human-readable string.**
(`src/codebugs/provenance.py:149-164`, `:550`, `:98-119`, `:670`)

The A row claims survival of *«переименование файла (если `file_status=renamed` уже найден)»*.
What `file_status` actually returns:

```python
def _verdict(status: str, reason: str) -> dict[str, Any]:
    return {"file_status": status, "reason": _displayable(reason)}
...
return _verdict("renamed", f"{file_path} renamed to {new_path}")
```

Two keys. The destination path is embedded in an English sentence, and `_displayable` runs
`encode("utf-8","surrogateescape").decode("utf-8","replace")` on it — so a non-UTF-8 rename
target is **irrecoverably** U+FFFD-mangled by design (the docstring at `:98-119` says so
explicitly). `check_findings` propagates only `reason` (`:670`).

Recovering `new_path` therefore means regex-parsing a diagnostic message — the precise heuristic
this repository refused for the merge gate (*"Not the commit message: parsing `Merge branch 'x'`
is the name-matching heuristic CB-57 refused"*, CLAUDE.md). And there is nowhere to put the
result: `file` is declared IMMUTABLE with a reason on both entities (CB-21 / BT-4), which BT-7
§0 itself lists as out of scope. So after a rename the anchor's own `file` still points at the
old path forever and A's headline advantage cell is unearned.

---

**FATAL-8: the batch path is never mentioned, and it is where the write-lock cost actually
lands.** (`src/codebugs/findings.py:1044` reached from `batch_add_findings`; `.claude/plans/CB-45-similarity-seam.md:2107`)

§7 bounds the risk as *«Чтение диапазона одного локального файла дёшево по ожиданию»* and
multiplies it only by "число мест". But `batch_add_findings` opens **one** `db.txn` and calls
`_add_one` per member with **no `annotate` parameter at all** — CLAUDE.md states this
deliberately (*"`batch_add_findings` is deliberately NOT the seam: it has no `annotate`
parameter, so import would silently run the similarity resolver per row inside the held write
lock"*). Confirmed by grep: `annotate` appears at `findings.py:891` (`_add_one`) and
`:1206/:1211/:1279` (`add_finding`) and **nowhere** in `batch_add_findings`.

So an anchor resolver performs **N filesystem opens + N line-scans inside a single
`BEGIN IMMEDIATE`**, blocking every concurrent writer. CB-45 already measured the analogous
cost for a pure-CPU resolver and accepted it *at that magnitude*: `~2.4 s/100-batch`
(CB-45 plan:2107). Adding cold-cache file I/O per row to a 100-row batch is a different order,
and §7's "образец" measurement plan is scoped to a single add. The document has no batch
number, no batch mitigation, and no acknowledgement that batch cannot opt out.

---

## SERIOUS

**SERIOUS-1: `_resolve_candidate` and `_repo_root` are PRIVATE; reusing them from `anchor.py`
violates the module rule the document is otherwise careful about.**
(`src/codebugs/provenance.py:59`, `:75`; CLAUDE.md "Module structure")

§1а: *«якорный резолвер ПЕРЕИСПОЛЬЗУЕТ `_resolve_candidate` и этот словарь, а не строит второй»*.
CLAUDE.md: *"They must NOT import each other's private functions — only public interfaces."*
Neither `_repo_root` (which the Р3(i) gate «файл читаем от корня worktree» requires) nor
`_resolve_candidate` has a public wrapper. Either provenance grows a public path-resolution API
(a real unit, not named in §9) or `anchor.py` grows a second copy — which is the drift shape
CB-22 and CB-57 both exist to forbid. The document assumes the free option.

**SERIOUS-2: §2's four options are CB-95's own four bullets, re-tabulated — the claim that the
table was "порождена из П1–П9, не перечислена по памяти" is false.** (CB-95 description, verified via `export-csv`)

CB-95's stored description already enumerates, in order: *CONTENT ANCHOR / SYMBOL ANCHOR /
GIT-BLAME-DIFF WALK / HYBRID with an explicit confidence-`reason` field*. §2's A/B/C/D are those
four, with a cost column added. That is fine as work; it is not fine as a methodology claim in a
repo whose recurring lesson is *"a rule expressed as an enumeration gets fixed at the sites
someone enumerated."* Two options that a genuine generation pass would have surfaced are absent
— see SERIOUS-3 and SERIOUS-4.

**SERIOUS-3: a separate anchor TABLE is never considered, and it is the option the repo's own
most recent precedent points at.** (`src/codebugs/relations.py:1-23`)

Р2 rejects only *«колонка»* (*"колонка делает версию схемой"*). `relations.py`'s opening
paragraph is the counter-argument, written in this repo two weeks ago:

> Relations were being recorded in ad-hoc JSON `meta` keys — 164 distinct key names for roughly
> five concepts… That substrate cannot answer "what is related to CB-123", and it cannot forget:
> `meta` writes are merge-only … so a key can be overwritten but never removed.

Every clause applies verbatim to `meta.anchor`. A table also dissolves §4's "one card, many
places" problem, which BT-7 concedes is the dogfood dialect (measured: **21 of 32** `file:line`
strings in this tracker cite more than one file) and then defers behind a trigger. Rejecting
"column" and not evaluating "table" is a partition error in the one decision that fixes the
shape for good.

**SERIOUS-4: option C is dismissed on an asserted cost against a strawman.** (§2 row C, §2 bullet 2)

C is characterised as `git log -L`/`blame` walking the whole history, *«стоимость O(история) на
каждое чтение»* — unmeasured, and not the only form. The cheap variant is a single
`git diff -U0 <captured_at_commit>..HEAD -- <path>` and a hunk-offset remap: **O(one diff)**,
not O(history), no blame, and it composes with the `-M` rename records `provenance.py:497-498`
already parses into `(old, new)` **pairs** (`_parse_rename_records`) — i.e. the structured
rename data FATAL-7 shows is missing from `file_status`'s public output but *does* exist inside
provenance. Nothing in the document measures either variant. "C отклонён" is therefore a
decision made on a number nobody produced.

**SERIOUS-5: registering `anchor` as a resolver key silently DESTROYS existing `anchor` values on
CSV import, and collides with an in-use key that means something else.**
(`src/codebugs/findings.py:1435-1439`; measured: 16 autosorter rows)

```python
dropped_keys = (
    _RESERVED_META_KEYS | _ADD_ONLY_RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
)
```
`import_findings` strips that union from every imported row's meta. The autosorter corpus already
carries `meta.anchor` on 16 rows, holding **card ids** (`"CB-1878"`, `"CB-1566"`) — a completely
different concept. After BT-7 lands, importing that export drops all 16 with no error and no
count. The document never checks whether the key name it is claiming is free; measuring it was
one line of the same sweep §1а already ran.

**SERIOUS-6: §7's I/O mitigation is self-contradictory and would mass-pollute meta on the live
corpus.** (`src/codebugs/db.py:444-452`; measured: 161 directory + 21 glob `file` values in autosorter)

§7: *«`silent` при любой ошибке I/O (`resolver_errors` — queryable)»*. These are opposites.
`run_pre_add_resolvers` on a raised exception does **both**:

```python
sys.stderr.write(f"[pre-add resolver '{resolver.name}' failed] {e}\n")
errors.append({"resolver": resolver.name, "error": str(e)[:500], "at": observed_at})
```

A `file` that is a directory raises `IsADirectoryError`; a glob raises `FileNotFoundError`.
Measured population in the corpus BT-7 itself cites: 161 directories + 21 globs + 203
non-existent relative paths = **385 of 3176 rows** would stamp `meta.resolver_errors` and emit a
stderr line per add. That destroys `query(meta_key="resolver_errors")` as the signal CB-45 built
it to be, and turns an MCP server's stderr into per-add noise. The correct shape (`return None`
on any I/O failure, never raise) is not what §7 says.

**SERIOUS-7: Р6's normalization has no minimum length, and the document cites the module whose
measured policy is exactly that.** (`src/codebugs/similarity.py:54-58`, `:164`)

Р6: *«trim, схлопнуть пробелы; без casefold»*. §7 concedes `return None` collides in hundreds of
places and answers with `context_hash`. But `similarity.py` — the cited precedent — carries:

```python
DEFAULT_THRESHOLD = 0.7
MIN_TEXT_LEN = 40
...
if len(query_norm) < MIN_TEXT_LEN:
```
with CLAUDE.md's reason: *"trigram Jaccard scores 'Bug 1'/'Bug 2' ≈ 0.8 and two empty strings
1.0."* BT-7 inherits the precedent's architecture and drops its one calibrated constant. Worse,
collapsing whitespace **erases indentation**, so in Python the identical statement at two nesting
depths hashes the same — and the single most common real edit (wrapping a block in `if`/`try`,
i.e. reindent) becomes invisible to both `hash` and `context_hash` simultaneously. There is no
stated minimum anchor length, no maximum, and no rule for a degenerate anchor (`)`, blank line).

**SERIOUS-8: Р5's `staleness_check(anchor=True)` collides with `check_findings`' cache key and
resolves against the server's ambient cwd.** (`src/codebugs/provenance.py:655-660`, `:798-835`)

```python
effective = _effective_commit(f)
cache_key = (f["file"], effective)
```
The cache is keyed by `(file, commit)`. An anchor probe reads the **working tree**, whose content
is not a function of `effective`. Two findings on the same file with the same effective commit
share one cached verdict — correct for `file_status`, wrong for an anchor whose whole job is
"where is it *now*".

And the MCP tool signature has no `project_dir`: `check_findings(conn, None, …)` → `cwd =
_ambient_cwd()`, which `provenance.py:16-32` documents can return **None because the directory was
deleted** (*"a long-lived MCP server outlives the git worktree it was started in"*). So anchor
resolution silently binds to whatever directory the server was launched in — which for this
tracker is explicitly allowed to be any directory at or below the repo root, and for a cross-repo
absolute `file` is unrelated entirely. The document treats "от корня worktree (CB-93)" as settled;
CB-93 settled it for git/stat probes reached through `file_status`, not for a new read surface.

**SERIOUS-9: `meta` cannot delete a key, so a `lost` anchor is permanent.** (`relations.py:5-9` citing `findings.py` `dict.update`)

Р3(iii) offers manual re-anchoring as the repair path. Overwriting works; **removal does not** —
`meta_update` merges. A card whose anchored code was deleted keeps a stale `meta.anchor` forever,
exported in every CSV, and `{"anchor": null}` leaves the key present holding null. CB-26's
"unrepairable annotation" argument, which Р3(iii) invokes in its own favour, is only half
satisfied.

**SERIOUS-10: `[0, 0]` is presented as a measured shape and does not occur.** (§4; measured: 0 occurrences of `[0,0]` or `[0]` across both corpora)

§4: *«`[0, 0]` arch-health = «не код» → якоря нет»*. I searched every `lines` list in both
corpora: **zero** occurrences of `[0, 0]` and zero of `[0]`. The document's own framing is
*"порождена из измеренных форм §1а, не придумана"*. This one was.

**SERIOUS-11: §5's backfill mechanism is destructive as written.** (§5, bullet 2)

*«честно только с `captured_at_commit` из ринга/колонки и **checkout'ом того коммита**»*. A
checkout inside the user's live worktree destroys uncommitted work and is exactly the class of
operation this repo's whole `tools/worktree-*.sh` harness exists to prevent. The non-destructive
form is `git show <commit>:<path>`, which needs no checkout and no lock. Floating a destructive
mechanism in a deferred bullet is how it gets implemented as written six weeks later.

**SERIOUS-12: §9's slicing omits the registration surface and the behaviour change Т-a alone
lands.** (`src/codebugs/db.py:1139-1151`, `src/codebugs/server.py:201-214`, `src/codebugs/cli.py` mode allowlist)

A new self-registering module must be added to `_ensure_modules_loaded()`, to `SERVER_NAMES`, and
to the CLI `--mode` allowlist (CLAUDE.md, "Architecture migration → Current rules for new code").
None appears in §9. More importantly: **Т-a on its own changes the behaviour of three existing
verbs** — `add` and `update` begin refusing `meta={"anchor": …}`, and `import` begins stripping
it (SERIOUS-5) — before `anchor_resolve` exists to justify any of it. §9 names a golden only for
Т-b. That ordering makes Т-a a silent contract break with no read surface and no test named for
it.

---

## WEAKNESS

**W-1: П8's "ловушка нормализации" is inert as stated, and the real inverse trap is unnamed.**
(`src/codebugs/findings.py:434-441`)
```python
if isinstance(v, str) and len(v) >= 3 and _is_volatile_meta_key(k)
```
The loop runs over `meta.items()` — **top level only**, **`str` values only**. A nested
`anchor` dict can never be reached, so neither the key `anchor` nor any subfield name
(`captured_at_commit`, which does contain `commit`) can affect the fingerprint. П8 flags a
non-trap. The real one, unstated: if a future version ever *flattens* the anchor
(`meta.anchor_commit = "abc…"`), that value becomes strippable and identity changes for every
row that carries it. Also worth noting explicitly, since the doc doesn't: resolvers run at
`findings.py:1044`, **after** `_derive_fingerprint` at `:938`, so the captured anchor cannot
influence the fingerprint of its own row. Р1's guarantee holds — for a reason the document never
gives.

**W-2: A's advantage cell and Р3(i)'s gate contradict each other on the out-of-git population.**
§2 credits A with *«работает на любом читаемом файле, включая вне git»*; Р3(i) gates capture on
*«файл читаем **от корня worktree** (CB-93)»*. Outside a repository `_repo_root` returns `None`
(`provenance.py:59-73`), so there is no root and the gate refuses. The single differentiator A is
recommended on is switched off by the recommendation's own precondition.

**W-3: misattributed measurement.** §7: *«CB-45 замерил 34.5 ms лексического резолвера под тем же
локом»*. `CB-45-similarity-seam.md` measures **23.7 ms** (`:231`, `:1369`, `:2107`). The 34.5 ms
figure is from `RFC-identity-graph-2026-08-17.md:213` and `:349`, a later re-measurement. Small,
but the document's stated method is «цитаты — грепаемым содержанием», and this one isn't.

**W-4: §6's scoping cites a stale decision set.** *«не блокируется его нерешёнными D1/D2/D3»*.
D1 is settled in the RFC itself (`RFC:272-274`: *"The review settled the technical half… Remaining
decision is only whether to pay the schema cost now"*) **and the cost has been paid** —
`src/codebugs/relations.py` exists, with `RELATIONS_SCHEMA` and a live `finding_relations` table,
registered in `SERVER_NAMES` as `coderelations`. S1 has shipped. §6 was written against the RFC
document, not against the tree.

**W-5: `confidence` is in Р5's return shape and defined nowhere.** *«возвращает `{status, line,
reason, confidence}`»* — no scale, no derivation, no vocabulary. `file_status`, the house-style
precedent the document invokes twice, deliberately has no such field; it has `reason`, a closed
token vocabulary. An undefined numeric confidence beside a closed status vocabulary is how the
two drift.

**W-6: "построчный seek" (§7) does not exist.** You cannot seek to line N without reading the
preceding N lines. A file-size cap therefore does not bound the anchor read; a real corpus value
is line **4189** (`meta.lines = {'B1': '4189', 'B2': '4265-4274', …}`). The mitigation as phrased
would not survive the measurement §7 asks for.

**W-7: encoding, binary content and file kind are entirely absent.** No decode strategy
(`errors=`), no binary detection, no refusal of FIFOs/devices/sockets — while `fsio.py` already
carries this exact taxonomy for the *write* direction (CB-76) and could have been read as
precedent. A `file` pointing at `/dev/stdin` inside the write lock blocks forever.

**W-8: Р5 keeps `get`/`query` pure, and never addresses how the owner's stated need is reached.**
The verbatim request is *"Ideally, with auto-resolution of the new line."* After BT-7 the resolved
line is available only from a verb an agent must know to call. No `attention`-style surfacing
(BT-5's precedent), no mention in the `get` response. The requirement is met by construction and
missed in practice.

**W-9: tail counts in §1а do not reproduce.** Re-measured against the same two files:

| claim | doc | measured |
|---|---|---|
| `lines` list total | ≥210 | **206** |
| list lengths 6…20 | 19 | **15** |
| `lines` `file:line` (autosorter) | 41 | **40** |
| `lines` prose (autosorter) | 6 | **7** |
| codebugs `file:line` / prose | 33 / 1 | **32 / 2** |

The head numbers (539/98/28/16/1; a-b 180; list 2/3/1/4/5 = 76/42/40/25/8; comma 61; int-str 43;
dict 2; `file` kinds 2778/203/161/21/8/5-ish) all reproduce. The document says *«выборочно
перепроверено (2)»* — the two that were checked were evidently head rows.

**W-10: the multi-location decision is made on the wrong axis.** §4 defers `meta.anchors: […]`
because *«мультиякорь умножает цену захвата внутри write-lock … на число мест»*. Measured, the
dogfood dialect is multi-site in **21 of 32** `file:line` rows and the corpus adds 43 more under
`sites`/`site`. So "one primary anchor" is not a simplification of the common case — it is a
decision to answer ~⅔ of this tracker's located cards partially. The cost cited (write-lock time)
is the same cost SERIOUS-6 shows is mishandled anyway, and it is bounded by a cap, not by the
schema.

---

## NITPICK

**N-1: П3 slightly misstates `file_status`' shape.** It "возвращает один из `current, modified,
renamed, deleted, unknown` + `reason`" — the returned key is `file_status`, and the `reason` for
`renamed`/`deleted` is free prose, not a member of the token vocabulary §1а lists (`provenance.py:149-164`, `:550-552`).

**N-2: "ровно два ПИСАТЕЛЯ" is true of the literal key in `src/`, not of the system.** Verified:
`findings.py:1352-1354` (CSV import) and `:3314-3315` (CLI `add -l`). But every MCP client calling
`add(meta={"lines": …})` is a writer, and the tool's own docstring advertises it:
*"meta: Optional JSON metadata (**lines**, module, rule_code, etc.)"* (`findings.py:2929`).

**N-3: "второе самореrистрирующееся расширение с нулём SQL" understates what changes.**
`similarity.py` really does issue zero SQL (grep: no `SELECT`/`INSERT`/`execute(`) and reads rows
only through the public `findings.similarity_candidates` (`findings.py:2091-2103`). `anchor.py`
would match on SQL and diverge on everything the zero-SQL argument was actually protecting:
it performs unbounded external I/O inside the caller's write transaction. The precedent
transfers the letter, not the reason.

**N-4 (confirmed, credit where due).** Several premises verified exactly as written and are
worth keeping: П1's zero readers of `meta.lines`; П6's two reserved sets verbatim
(`findings.py:235`, `:307`); П7's auto-capture (`findings.py:2953`, `:3015`); П8's
`_VOLATILE_KEY_TOKENS` tuple verbatim (`findings.py:245`); §1а's `--meta`-beats-`-l` trap,
reproduced exactly (`findings.py:3314-3317`) and correctly flagged as a CB-15-class card; §7's
"resolvers run under the write lock" (`findings.py:1265` `db.txn` → `:1044` resolvers); Р3's
statement that pre-add resolvers do **not** fire on a bump (the `live`/`closed` branches return at
`findings.py:964`/`:989`, well before `:1044`); no `import ast` anywhere in `src/`; no parser
dependency in `pyproject.toml`; commit `f0b4010` is the CB-92/CB-93 merge; the CB-95 verbatim
quote is exact.

---

## Summary Scorecard

| Category | Count |
|---|---|
| FATAL | 8 |
| SERIOUS | 12 |
| WEAKNESS | 10 |
| NITPICK | 4 |

### The three that must be fixed before this reaches the owner

1. **FATAL-1** — Р3(i)+Р4 cannot both be ratified. Either the capture is not a pre-add resolver,
   or `anchor` is reserved-with-`updatable_keys` and Р4 is rewritten to say so.
2. **FATAL-6** — §8 question 1's package sentence contradicts the document's own ★recommendation.
   The owner cannot answer it correctly.
3. **FATAL-2 + FATAL-3 + FATAL-4** — §4's grammar is built on a misread contract, a branch that
   measures to 0–2 rows, and a key census that missed 61 located rows. The grammar has to be
   re-derived from a shape-based sweep, not a name list.

### The one that must not be lost

**FATAL-5.** Everything else is a design document defect. That one ships an arbitrary-file-read
into an MCP tool and writes the bytes into an exportable database.
