# BT-7 v2 — hostile adversarial review (Opus, round 2)

Target: `.claude/plans/BT-7-location-anchor.md` (v2, 2026-08-22, 313 lines).
Prior round read in full: `review-x2-bt7-judge-verdict.md` (12 mandatory fixes),
`review-x2-bt7-defender.md`, `review-x2-bt7-opus-attack.md`, `review-x2-bt7-codex-attack.md`.
Everything below was verified by reading `src/` or by running read-only measurements over
`/home/faxik/.cache/codebugs-identity/corpus.csv` (3176 rows) and
`codebugs export-csv /dev/stdout` (130 rows, live, today).

**Headline.** v2 is a large honest improvement over v1 — the premises are re-derived, the
grammar is re-swept, the seam contract is stated as the code actually enforces it. But the
rebuild bought its structure with a storage decision that **destroys the design's own repair
path**, justified by a benefit that **measures zero on both corpora**, behind a read boundary
**anchored to the wrong root**. Three of the twelve mandatory fixes are honoured in letter and
falsified in fact; five more are partial. The document is not ratifiable as written.

---

## FATAL

### F-1. Under the ★ storage choice a bad anchor is UNREPAIRABLE and UNRETRACTABLE — the ★ storage kills the ★ repair path

v2 makes two decisions that cannot both stand:

- **Р3 (line 165):** the reader is "новейший пригодный с хвоста, фолбэк на `meta.loc`". The **ring
  wins** over the top-level projection.
- **Р3 (lines 170–172):** the repair path is `update_finding(meta_update={"loc": …})`, legal
  "благодаря `updatable_keys=("loc",)`", plus `anchor_recapture`.
- **Р2 (line 152):** retraction is `loc: null`, "читается как `unknown(retracted)`".

Both repair mechanisms write the **top-level** `meta.loc`. Neither can touch a ring entry:

- `findings.py:235` — `_RESERVED_META_KEYS = frozenset({"occurrences", "occurrences_dropped",
  "regressed", "recurrence_of"})`.
- `findings.py:276-288` (`_validate_meta_keys`) — on update the reserved set is reduced only by
  `db.resolver_updatable_meta_keys()`. `occurrences` is not, and cannot be: it is core's key, not
  a resolver's, so no `updatable_keys` declaration can ever open it.
- `db.py:373` — `run_pre_add_resolvers` passes `forbidden=_RESERVED_META_KEYS`, so a resolver
  cannot write into the ring either.
- `findings.py:2820`, `:3314` — `update_finding`'s own docstrings state the invariant outright:
  *"THE OCCURRENCE RING IS NOT REWRITTEN. `meta` never appears in an UPDATE here."*

So under 2.2(b): a `loc` captured against the wrong file, a `loc` whose `text` leaked bytes the
owner later rules out under q4, a `loc` written by a buggy first version of `loc.py` — **cannot be
corrected, cannot be cleared, and cannot be retracted**, because the reader prefers the entry that
no API can reach. Writing `meta.loc = null` retracts nothing while any ring entry carries a `loc`.

This is precisely the shape the document itself twice declares forbidden: Р3 line 171 and CLAUDE.md's
CB-45/CB-26 rule — *"неремонтируемая аннотация — это форма CB-26"*, the reason `similar_to` got
`updatable_keys` in the first place. v2 reproduces CB-26 **inside the fix for it**.

The judge downgraded SERIOUS-9 ("a `lost` anchor is permanent") on the explicit ground that it is
*"moot under 1(b)/(c)"*. That ruling is wrong on the code, and v2 inherited it without checking.
The ring does not dissolve non-removability — it **makes it total**, and worse than (a), because
under (a) at least one `meta_update` fixes the one place the reader looks.

Aggravating: `findings.py:395-396` — `_OCC_KEEP_FIRST = 10`, `_OCC_KEEP_LAST = 10`, and
`:632-637` keeps `ring[:10] + ring[-10:]`. The **first ten entries are never evicted**. An anchor
in entry 3 is immortal for the life of the card. §2.2's "Отзыв" cell for column (b) —
"тумбстоун в проекции" — is false, and it is false in the direction that flatters the ★ choice.

**Fix required:** either the reader must prefer top-level over ring (which discards C-A4's whole
point), or the ring entry needs a core-level rewrite path (a new mutation contract on
`occurrences`, which is a separate ratified decision, not a BT-7 clause), or storage returns to
(a)/(c). This is a re-decision, not a paragraph.

### F-2. The measured re-observation rate is ZERO in both corpora — Р3(в) and q2(в) buy nothing they claim

Р3/§2.2/q2 choose per-occurrence capture over insert-only on two stated benefits (lines 131,
276-277): *"каждое наблюдение → старые карты получают якорь при следующем наблюдении"* and *"все
§5-триггеры становятся измеримыми"*. Both are conditional on bumps happening. v2 never measures
the bump rate — in the very section the judge said was the failure mode of v1 ("sixteen findings…
all in the four sections that claim to be measured").

Measured, read-only, today:

| corpus | rows | rows with `occurrence_count > 1` | rows carrying a `meta.occurrences` ring |
|---|---|---|---|
| codebugs (live export) | 130 | **0** | **0** |
| autosorter (`corpus.csv`, 2026-08-17) | 3176 | **0** | **0** |

Dedup (CB-43) landed **2026-08-16** (`e7c7111`). **88 of the 130 live codebugs rows were created on
or after that date** — six days of heavy dogfood filing through the identity function — and not one
produced a second observation. The autosorter snapshot is one day post-CB-43, so its zero is
expected and I do not lean on it; the codebugs 0/88 is not.

Consequences, all of which contradict the document as written:

1. "Старые карты получают якорь при следующем наблюдении" (line 131, repeated at 277) is, on the
   measured rate, **never**. Under (b) an old card acquires an anchor only through Т-c backfill —
   which §5 line 238 keeps *optional*. The judge's mandatory item 3 anticipated exactly this: *"If
   the owner rejects per-occurrence capture, §5 must say plainly that its triggers are unmeasurable
   and Т-c moves from optional to required."* v2 took the third option and kept Т-c optional
   anyway, on the strength of a rate it did not measure.
2. §5's triggers (line 233: "все триггеры теперь ВЫЧИСЛИМЫ — популяция per-observation") are
   computable over **new inserts only** — the identical population as v1's (a). C-R11, which the
   judge called "the single strongest structural finding in either report", is therefore **not
   dissolved**; it is renamed.
3. The new seam (J-1's "observation enricher") exists *solely* to reach the bump path. On the
   measured rate it fires zero times. v2 pays a new core registry for a path that has never
   executed in this tracker's history.

**This does not kill (b)** — a re-observation regime may arrive with automated filers — but it
kills the *argument* for (b) as presented to the owner, and q2's cost/benefit line (277) is
currently unanswerable: the owner is asked to buy a seam for a benefit whose measured magnitude is
zero and which the document does not disclose. Under the owner's standing rule ("the options **with
the cost of each**"), q2(в) must carry this number.

### F-3. The read boundary is anchored to a root that provably is not the one it claims to be identical to

§7а (line 254): *"По умолчанию — containment `commonpath == root`, идентичный `file_status`"*.
Р5 (line 187): *"Биндинг: `project_dir` явный, дефолт через `db.describe_root()`"*.

These are two different roots, and the document asserts they are one:

- `provenance._repo_root` (`provenance.py:59-73`) → `git rev-parse --show-toplevel` from the cwd,
  `realpath`'d. `_resolve_candidate` (`:75-96`) documents it in capitals: *"`root` is the WORKTREE
  ROOT, not the process cwd (CB-93)."* That is the root `file_status`' containment is computed
  against (`:345`, `out_of_repo (worktree {root})`).
- `db.describe_root()` (`db.py:1019-1061`) returns `os.path.dirname(os.path.dirname(path))` where
  `path` is `<root>/.codebugs/findings.db` — the **TRACKER root**, selected by
  `project_dir > --tracker-root > $CODEBUGS_ROOT > walk`.

They coincide only in the ordinary discovered-walk case. They diverge exactly in the population
this document invokes to justify itself: `$CODEBUGS_ROOT`/`--tracker-root`/`--repo` bindings and
cross-repo absolute `file` values (П9, line 76; CB-88/89/91, line 258). With a central tracker
declared at `/home/x/trackers/central`, `commonpath == describe_root().root` would **admit** any
file under the tracker directory and **refuse** every file in the repository the finding is about
— wrong in both directions, on the one predicate in the document that is a capability boundary
rather than a data-quality choice. That is FATAL-5's residue landing in FATAL-5's own fix.

Second defect in the same clause: `describe_root()` **can return `root: None`** — the
`DatabaseNotFoundError`/`OSError` branch (`db.py:1035-1043`) returns `"root": declared`, which is
`None` when nothing was declared. Р5 forbids `_ambient_cwd()` because SERIOUS-8 established it "can
be `None`". The replacement has the identical nullability and v2 does not say what happens then.
One nullable ambient source was swapped for another and the swap was declared a fix.

**Fix required:** name the root explicitly — *worktree root of the resolved `project_dir`, via the
public path API Т-0 is already creating* — and state the `None` behaviour (refuse, with
`unknown(no_root)`, is the only fail-closed answer).

---

## SERIOUS

### S-1. One payload, TWO carriers, neither priced — and the resolver degenerates into a key-reservation device

Р3 (lines 160-164) routes the same precomputed object down two different paths: insert →
pre-add resolver → top-level `meta.loc`; re-observation → **new** generic pre-lock "observation
enricher" → `meta.occurrences[*].loc`. The judge's mandatory item 2 asked for **the** carrier seam,
named and priced. v2 names two and prices neither.

What the second path actually costs, read against the code:

- **A seventh registry in `db.py`.** Existing: `_schema_registry:48`, `_tool_providers:114`,
  `_cli_providers:149`, `_post_add_hooks:178`, `_status_change_hooks:216`, `_pre_add_resolvers:271`.
  The enricher is genuinely new (v2 is honest about this) — and it must call
  `_ensure_modules_loaded()` first for the same reason `resolver_reserved_meta_keys` does
  (`db.py:333-346`, the Codex diff-review lesson about a bare library connection running an empty
  registry). Not stated.
- **Three core signature changes**, because the observation is a literal, not an object:
  `_add_one` (`findings.py:877-896`) needs a new parameter; `_occurrence_entry`
  (`:483-524`) needs a new key; the resolver observation dict is built literally at `:1050-1064`
  with a closed key set and needs a new member.
- **A fourth opt-out flag with its AST ratchet test.** `escalate`, `promote_tags` and
  `gate_category` each have exactly one call site (`import_findings`) pinned by a ratchet
  (`TestEscalateOptOutRatchet`, `TestPromoteTagsOptOutRatchet`, `TestGateCategoryOptOutRatchet`).
  A ring-resident `loc` needs the same treatment or the "import is not an observation" rule is
  prose again. v2 says only "импорт вырезает `loc`" (line 153), which describes the *top-level*
  strip and not this at all.

And the structural objection: once the core carries the payload for the bump path, **the resolver
computes nothing**. It becomes a pass-through whose only remaining function is to make
`resolver_reserved_meta_keys()` contain `"loc"` (Р4, lines 174-178). That inverts CB-45's seam — a
resolver is supposed to be the thing that *derives* the annotation inside the transaction. Using it
as a key-reservation token while the work happens in a different, newer seam is the "letter of the
precedent without its reason" that N-3 already flagged once and v2 acknowledges (line 163) for a
different clause. Either the core writes both destinations (and the reservation needs its own
mechanism), or the enricher writes both (and the resolver is deleted). Two routes into two
destinations with two validation points and two opt-out semantics is the "sharing an implementation
does not share a decision" defect from CLAUDE.md, one level up.

### S-2. Р1's structural guarantee is re-asserted against the OLD mechanism while the NEW one sits on the wrong side of the line

П8 (lines 72-74) and Р1 (line 142): the anchor cannot influence its own row's fingerprint because
`_derive_fingerprint` runs before the resolvers. Verified — `_derive_fingerprint` is called at
`findings.py:938`, `run_pre_add_resolvers` at `:1050`.

But the guarantee is a statement about **where the resolver runs**, and v2's primary capture no
longer happens there. It happens pre-lock, and the document says only that the object "едет в
observation" (line 158). `_add_one` has no observation object; it has a `meta` parameter, and
`_derive_fingerprint(category, file, description, meta)` (`:938`) consumes it —
`_normalize_for_fingerprint` (`:433-441`) strips top-level `str` values under volatile-looking keys.
If the enricher payload is merged into `meta` before `_add_one` (the obvious implementation, since
`meta` is the only per-observation dict the function takes), the guarantee is broken by
construction the day someone stores a `str` under a key matching `_VOLATILE_KEY_TOKENS`
(`:245`: `sha, commit, slug, branch, log, run, time, duration` — matched by **substring**, so
`loc_commit`, `capture_time`, `anchor_run` all qualify).

Today's `loc` object is a `dict`, so nothing is stripped and the hazard is latent — which is
exactly П8's own "inverse trap" (flattening). The point is that v2 states the guarantee as
structural and then adds a mechanism that runs on the unguarded side of the same boundary, without
saying the carrier must never be `meta`. **Р1 needs one sentence: the enricher payload travels as a
sibling of `meta`, never inside it, and a test pins that `_derive_fingerprint`'s inputs are
unchanged by capture.**

### S-3. §4's fix for FATAL-4 routes `sites`/`site` to a grammar branch that cannot read them — and the adopted branch is the one branch left unquantified

§4 line 215: *"`sites`/`site` (61 строка, пропущены v1): та же обработка, что `lines`-строка/список."*

The `lines`-string branch is `int, "a-b", "N,M,K-L"` (line 209) and the `lines`-list branch is
`list[int]` (line 211). Measured shapes of the 43 autosorter `sites`/`site` values:

```
sites/site forms: {('sites','str'): 10, ('site','str'): 13, ('sites','list'): 18, ('sites','dict'): 2}
sample: 'entity_domain/domain.py:1123-1172; quality/remediation.py:183,269; quality/mode…'
        ['bridges/common/push_supervisor.py:126-146', 'bridges/common/change_hint.py:215…']
```

Not one of them is an int, an int-string, an `"a-b"`, or a `list[int]`. They are **`path:range`
tokens**, i.e. they belong to the `file:N` branch (line 216-219), not to the `lines` branch. Read
literally, §4's remedy for FATAL-4 captures **zero** of the 61 rows it was written for. Read
charitably (route them to `file:N`), the measured yield is:

```
distinct-filename-count per row: {0: 5, 1: 18, 2: 8, 3: 8, 5: 4}
under the v2 gate: multi_filename→ambiguous 20 | unique+basename match 16 | mismatch 2 | no token 5
```

16 of 43. The plural key is plural *by construction* — that is what "sites" means — so the branch
that refuses multi-filename rows refuses about half of the population the reviewers forced into the
census. That is a legitimate design answer, but it must be **stated with the number**, not hidden
behind "та же обработка".

Second half, and this is the direct miss against mandatory item 5 ("*justified by the MATCHING
count*"): §1а and §4 publish the counts for the spelling v2 **rejected** — "совпадение токена с
колонкой `file` — 2/31 точных", "0 точных совпадений" (lines 84, 90, 218) — and publish **no count
at all** for the basename gate it **adopted**. I measured it:

| corpus | `lines` rows | with `NAME.ext:N` token | multi-filename (refused) | **basename match (captured)** | mismatch |
|---|---|---|---|---|---|
| codebugs (live) | 44 | 32 | 12 | **19** | 1 |
| autosorter | 539 | 40 | 21 | **17** | 2 |

Plus 16 from `sites`/`site`. So the branch is alive and worth keeping — the document simply never
says so with evidence, in the section whose entire remit was to be re-measured. (Bonus: the "2/31"
denominator at line 84 is unexplained; the token population is 32 and the multi-file subset is 12.)

### S-4. C2's cost cell is wrong — provenance has a rename-PAIR parser, not a hunk parser, and they are different git invocations

§2.1 C2 (line 119) prices diff-hunk remap with *"парсер rename-записей уже есть в provenance"*, and
§9's Т-0 repeats it (line 290). What exists is `_parse_rename_records`
(`provenance.py:167-197`), whose docstring pins its input precisely:

> `(old, new)` pairs from `git diff --name-status -z --diff-filter=R` … `-z` emits
> `<status>\0<old>\0<new>\0` per rename

It splits on NUL, checks `len(fields) % 3`, and requires every record to start with `R`. It cannot
read `@@ -a,b +c,d @@` hunk headers, and it cannot read the output of the command C2 actually
specifies (`git diff -U0 --find-renames <captured>..HEAD`), which is a unified diff with
`diff --git` / `rename from` / `rename to` / `@@` lines and no NUL framing at all. Feeding
`-U0 --find-renames` output to this parser returns `None` (desync) on the first record.

So the one cost cell that makes C2 look cheap is **zero percent reused**. The hunk-header parser,
the offset arithmetic, the rename-aware path switch and the "captured commit is unreachable"
degradation are all new. This is C-S3's finding ("cost table has unmeasured numbers stated as
fact") reappearing in the row v2 *added* in response to C-S3's sibling SERIOUS-4. Mark it
`(оценка)` and say what is actually reusable: the `-z`/`--diff-filter=R` **invocation discipline**
and the fail-closed `None`-not-`[]` lesson — not the parser.

### S-5. `staleness_check(anchor=True)` inherits `_ambient_cwd()` — two roots in one response record

Р5 (lines 181-188) offers two read surfaces and states the binding rule for one of them. The other,
`staleness_check`, is an existing MCP tool with **no `project_dir` parameter**
(`provenance.py:801-838`), which calls `check_findings(conn, None, …)`, which resolves
`cwd = project_dir or _ambient_cwd()` (`:606`).

So a record returned by `staleness_check(anchor=True)` would carry a `file_status` computed against
`_repo_root(_ambient_cwd())` and an `anchor` sub-verdict computed against `db.describe_root()` —
**two answers to "which directory" inside one JSON object**, which is verbatim the CB-11/CB-49
lesson the judge invoked when he raised SERIOUS-8. Р5's nesting rule (line 184: nest so `reason`
has one producer) solves the vocabulary collision and creates the root collision one field over.

Either `staleness_check` gains `project_dir` (a wire-golden change, unmentioned in Т-b, line 295),
or the anchor surface is `anchor_resolve` only and the `staleness_check(anchor=)` half is dropped.

### S-6. `get(resolve_anchor=True)` cannot be built without core learning the extension, or a third registry

Р5 line 189-191 proposes `get(resolve_anchor=True)`, and argues it preserves purity because `get`
does not acquire file I/O *"молча"*. Three problems, none addressed:

1. `get_finding` is four lines of pure SQL (`findings.py:2198-2203`). Resolution lives in `loc.py`,
   an extension. For `findings.get_finding` to resolve, **core must import or call the extension** —
   the exact rule v2 cites approvingly at line 178 and CLAUDE.md states as *"core must not know an
   extension's key names"*. The only compliant answer is a **third** registry (a read-side
   annotator), which is a further unpriced seam.
2. `get_finding` has no `project_dir` either, so F-3's binding problem lands here a third time.
3. "не приобретают файловый I/O **молча**" is doing load-bearing work in that sentence. `get`
   acquires file I/O; the adverb only says it is opt-in. Say it plainly, because W-8's whole point
   was that the owner asked for auto-resolution and an opt-in flag on a tool an agent must know to
   pass is a partial answer to that request, not a full one.

### S-7. The failure vocabulary is three sets, not the one the judge mandated

Mandatory item 8: *"one closed classification at all three sites."* v2 has:

- **Capture side** (§4, lines 226-227): `not_a_file | out_of_repo | unreadable | too_large |
  binary | no_grammar | ambiguous_multisite` (7).
- **Read side** (Р2, lines 148, 152): `unsupported_anchor_version`, `retracted` — neither in the
  §4 list.
- **Status enum** (Р5, line 183): `current | moved | lost | ambiguous | unknown`.

Nowhere does v2 say whether these are one vocabulary or two closed vocabularies with a declared
boundary. `ambiguous` appears in both the status enum and (as `ambiguous_multisite`) the capture
reasons with different meanings. `out_of_repo` collides by name with `provenance.file_status`'s own
`out_of_repo (worktree …)` reason (`provenance.py:345`) while carrying no worktree parameter. This
is SERIOUS-6 ("three incompatible failure classifications") surviving its own fix in a new
distribution.

### S-8. "The ring automatically inherits import-strip / restore-verbatim semantics" is half false, and the false half is the interesting one

Р3 (lines 168-169) presents this as the bonus that makes (b) contract-free. Measured against the
code:

- **restore-verbatim: true.** `restore_findings` (`findings.py:1604-1700`) writes `meta` through
  `_restore_json` verbatim and bypasses `_validate_meta_keys` entirely. A ring `loc` round-trips. ✓
  (And this is why Р2's read-side validation is genuinely required — that argument is correct.)
- **import-strip: not inherited, because there is nothing to strip.** `_import_meta`
  (`findings.py:1332-1355`) filters **top-level keys only**, and `occurrences` is in
  `_RESERVED_META_KEYS` (`:235`), so an imported row's **entire ring is discarded** before any key
  filtering could reach a nested `loc`. The contract a ring `loc` "inherits" is *total loss*, not
  *selective strip*.

Two consequences v2 should state rather than inherit:
(a) an export→import round trip loses every anchor **and** every occurrence, so the
per-observation population F-2 depends on does not survive the one operation peers use to share
findings;
(b) a *new* ring entry created on the import path (a fingerprint hit on a live local row still
bumps — `import_findings` docstring, `:1414`) would be built by `_occurrence_entry` locally, so the
"import is not an observation" rule needs the fourth opt-out flag from S-1, not the top-level strip
v2 describes at line 153.

### S-9. The ring cap is not a rolling window, and the anchor's ring cost is understated

q2(в)'s cost line (line 276) is *"место в ограниченном ринге"*. `findings.py:395-396`,
`:632-637`: overflow keeps `ring[:10] + ring[-10:]`. **The first ten entries are permanent** — they
are the oldest observations and are never evicted. An anchor object (`text` + two hashes + two
context fields + commit) in entries 1-10 is carried in every `SELECT *`, every `export-csv` row,
every `restore`, forever, for the life of the card. `_OCC_DESC_CAP = 2000` (`:397`) caps only
`description` (`:517`); `entry["meta"] = meta` (`:521-522`) is **already uncapped**, so the ring has
no total-size discipline for v2's new cap to hang off. "Ring-size cost (J-1)" needs a number, and
the number is `MAX_TEXT_BYTES × 20` per card floor, of which half is immortal.

---

## WEAKNESS

- **W-1. The INVARIANTS block does not invariant its own biggest fields.** Р2 (lines 146-151) caps
  `text` (`MAX_TEXT_BYTES`) and the span (`MAX_ANCHOR_LINES`) but says nothing about
  `context_before`/`context_after`, which are listed as object fields at line 146 while Р6 line 199
  says *"ширины записываются в объект"*. Are they the context **text** or the context **widths**?
  If text, they are uncapped in a block whose purpose is caps; if widths, the context text is
  unstored and `context_hash` cannot be re-verified. Pick one and cap it.
- **W-2. Every cap is a name, not a number.** Mandatory item 4 asked for "numeric caps"; §7а
  (line 256) defers all three to Т-a. Defensible for `MIN_ANCHOR_CHARS` (Р6 correctly says it must
  be *calibrated*, per the `MIN_TEXT_LEN=40` lesson — `similarity.py:58`), much less so for
  `max_bytes_read`, which is the containment/DoS bound and the one number the owner is being asked
  to ratify a boundary around.
- **W-3. §1а's own third-digit disclaimer is violated one section later.** Line 103: *"публикуется
  порядок, не третья цифра"*; line 116 (§2, row A): *"заякорить может ~2787/3176"*. Also
  2787 + 381 = 3168 ≠ 3176 (8 rows unaccounted).
- **W-4. The codebugs measurement is undated** while the autosorter one carries a snapshot date
  (line 86). Line 81 says 128 rows; the live tracker is 130 today. Date it.
- **W-5. Т-c's backfill has no commit to read from.** §5 line 238 / §9 line 296:
  `git show <commit>:<path>`. Which commit? Old rows have no `captured_at_commit`; the only
  candidates are the frozen `reported_at_commit` column or `provenance._effective_commit`
  (`provenance.py:555-585`) — and for rows with an empty ring those are the same thing, which is
  fine, but it must be *said*, and the `unreachable_commit` degradation (`provenance.py:375`) must
  be in the reason vocabulary. It is not (S-7).
- **W-6. `anchor_recapture` and loc.py's "zero-SQL" claim.** Line 172 promises a server verb; line
  162 promises `loc.py` is the second zero-SQL extension. Both hold only if the verb writes through
  `findings.update_finding(meta_update=)` rather than issuing its own UPDATE. Say so — and note
  that under (b) it writes the field the reader deprioritizes (F-1).
- **W-7. The pre-lock object is deep-copied per resolver.** `db.py:433`:
  `resolver.fn(conn, copy.deepcopy(observation))`, once per registered resolver, inside the write
  lock. With the anchor in the observation, `similarity`'s resolver also receives a full copy of
  `text` + hashes on every add. Small, but it is inside the lock the whole two-phase restructure
  exists to unburden.
- **W-8. `describe_root()`'s nullability is unhandled** (see F-3, second half) — listed here too
  because the one-line fix is separable from the root-identity fix.
- **W-9. The 2.4 s/100 extrapolation is unmarked as an estimate.** Line 105-106: 23.7 ms × 100 =
  2.37 s ✓ arithmetically, but the similarity resolver's cost is not constant across a batch — its
  candidate pool grows as earlier members insert (`similarity.py:154-250`, newest-500 pool). C-S3
  demanded `(оценка)` marks; this cell is the one the whole two-phase argument leans on and it
  carries none.

---

## NITPICK

- **N-1.** П3 (line 39-42) calls the `unknown` reasons "закрытый словарь токенов". Two of the
  sixteen are token **plus payload**: `relative_git_env ({relative_env})` (`provenance.py:310`) and
  `out_of_repo (worktree {root})` (`:345`). Cosmetic, but the anchor is said to "inherit" that
  vocabulary and the inheritance is of a shape that is not uniform.
- **N-2. `loc` is free — verified, and the verification is worth recording in the document**:
  0 of 3176 autosorter rows and 0 of 130 live codebugs rows carry a top-level `meta.loc`. Also
  verified: `anchor` = 16 (autosorter, values are card ids: `CB-1878`), `location` = 1, `sites` = 30,
  `site` = 13, `lines` = 539, `line` = 98, `function` = 28; codebugs `sites` = 18, `lines` = 44.
  Every §1а census number reproduces exactly. Caveat worth one line in v2: the neighbourhood is
  crowded — `loc_src`, `loc_tests`, `loc_blocker`, `proposed_loc`, `handler_loc`, `root_cause_loc`,
  `repo_loc`, `est_size_loc`, `fix_locus` all exist in the autosorter corpus — and nothing will
  keep `loc` free after ratification except the reservation itself.
- **N-3.** "Парсеров в пакете нет (`import ast` — 0)" — verified, 0 hits in `src/`. ✓
- **N-4.** The `[0, 0]` provenance claim is verified: six call sites in
  `/home/faxik/.claude/skills/arch-health/state/pending-2026-08-17-arch-health.py:195,224,245,265,287,302`.
  The document's path elision (`arch-health/state/…-arch-health.py`, line 96) is accurate.
- **N-5.** `f0b4010` verified: *"Merge fix/cb-92-cb-93-one-coordinate-system … (CB-92, CB-93)"*. ✓
- **N-6.** §9's Т-a "три регистрации" is right (`_ensure_modules_loaded` at `db.py`, `SERVER_NAMES`,
  `--mode`) — `similarity` is present in all three today, so the precedent is real. The **fourth**
  registration the enricher itself needs (a new `register_observation_enricher` in `db.py`) is not
  in the slice.
- **N-7.** §1а line 84's "2/31" vs line 81's "32 строки" — unexplained denominator (see S-3).

---

## Mandatory-fix audit

| # | Judge's mandatory fix | v2 | Evidence |
|---|---|---|---|
| 1 | Storage: three options on ONE comparison; rename the key | **PARTIALLY** | §2.2 does give a 3-column × 6-row table and renames to `loc` (verified free: 0/3176, 0/130). But the "Отзыв" cell for (b) — *"тумбстоун в проекции"* — is **false**: `occurrences` ∈ `_RESERVED_META_KEYS` (`findings.py:235`) and is refused on update (`:276-288`), so the ★ column is scored on a property it does not have (**F-1**). |
| 2 | Capture outside the lock, two-phase; **name and price the carrier seam** | **PARTIALLY** | Two-phase is stated (Р3, lines 156-159) and the seam is named as new (line 164) — genuinely new, verified: `db.py` has six registries (`:48,114,149,178,216,271`), none pre-lock. Pricing is one clause. Unpriced: 7th registry + `_ensure_modules_loaded` discipline, three core signature changes (`_add_one:877`, `_occurrence_entry:483`, observation literal `:1050`), a 4th opt-out flag + AST ratchet, and the fact that **two** carriers are specified, not one (**S-1**). |
| 3 | Capture population = every observation | **HONOURED IN LETTER, FALSIFIED IN FACT** | Р3/q2(в) choose per-observation. Measured bump rate: **0/130 codebugs (88 post-CB-43), 0/3176 autosorter**; zero rows carry a ring. The stated benefits (old cards get anchors; §5 triggers become measurable) do not occur at that rate, and Т-c stays "опционально" (line 238) which the judge conditioned on rejecting this option (**F-2**). |
| 4 | Containment boundary is an owner decision; §7а; `S_ISREG`; caps; never raises; q4 | **PARTIALLY** | §7а exists, q4 exists, `S_ISREG`-only mirrors `fsio.py:182-187` ✓, "капы ЧИСЛАМИ в Т-a" defers the numbers (**W-2**), and the containment root is specified as `db.describe_root()` while being claimed *"идентичный `file_status`"* — two different roots (`db.py:1046` vs `provenance.py:59-73`), nullable, in the cross-repo population the doc itself cites (**F-3**). |
| 5 | Grammar re-derived by SHAPE sweep; `file:N` justified by the MATCHING count | **PARTIALLY** | The sweep predicate is published (line 206) ✓; `list[int]` = individual lines ✓; `[0,0]` cited to its writer ✓; residue published as regex ✓. But `sites`/`site` are routed to a branch that cannot parse them (**S-3**), and the adopted basename gate is the one branch with **no** published match count — measured here as 19/32 + 17/40 + 16/43. |
| 6 | Р3(i)+Р4 rewritten: registration IS reservation; delete the `fingerprint` analogy | **HONOURED** | Р4 (lines 174-178) states it exactly as the code behaves (`findings.py:278-281`, `db.py:333-358`); the analogy is deleted and said to be deleted. |
| 7 | Owner questions rewritten; q1/q2 non-contradictory; q4 new | **HONOURED** | §8: q1 strips "обновляется наблюдением" and says so (line 268); q2 is three-way with per-option costs; q4 added. Residual: q2(в)'s cost line must carry F-2's measured zero to satisfy the owner's "cost of each" rule. |
| 8 | ONE closed failure classification at all three sites | **PARTIALLY** | Capture-side vocabulary (7 tokens, line 226) is closed and `resolver_errors` is correctly kept as the broken-resolver signal ✓. But two more read-side reasons live outside it (`unsupported_anchor_version`, `retracted`) and the status enum overlaps it by name (`ambiguous`) — three sets, no declared boundary (**S-7**). |
| 9 | Contract completeness: INVARIANTS, hash spec, `context_hash` required, nested verdict, `confidence` deleted | **PARTIALLY** | INVARIANTS block ✓ (`v∈{1}`, `line>=1` int-not-bool, `end>=line`, hex-length, 40-hex commit); hash fully specified per C-M2 ✓ (sha256 over a JSON **array**, 32 hex, EOL first, context excludes anchor lines); `context_hash` required with actual widths at file edges ✓; verdict nested under `anchor:` with `resolved_against` ✓; `confidence` deleted ✓. Gap: `context_before`/`context_after` are typed ambiguously and uncapped (**W-1**). |
| 10 | C split into C1/C2; cascade C2→A→unknown; rename cell backed by Т-0 | **PARTIALLY** | The split, the cascade and Т-0 are all there ✓, and C1's cost is honestly marked as asserted-not-measured ✓. But C2's cost cell claims a parser that does not fit: `_parse_rename_records` (`provenance.py:167-197`) reads `--name-status -z --diff-filter=R`, not `-U0 --find-renames` hunks (**S-4**). |
| 11 | Binding and caching for the read surface | **PARTIALLY** | The `(file, effective)` cache is correctly excluded ✓ (`provenance.py:655-667` — the key really is wrong for a working-tree probe). The binding half fails twice: `describe_root()` is the tracker root, not the worktree root (**F-3**), and `staleness_check` has no `project_dir` at all (`provenance.py:801-838`), so the anchor sub-verdict and `file_status` in one record resolve against different roots (**S-5**). |
| 12 | §9 re-sliced Т-0/Т-a/Т-b/Т-c with registrations and the behaviour change | **HONOURED** | §9 lines 288-296: Т-0 (public path API + structural `new_path`), Т-a (module, grammar, normalizer, invariants, three registrations, `updatable_keys`, tests for the add/update/import behaviour shift it causes, batch measurement), Т-b (read surface + wire golden), Т-c (backfill via `git show`). The only omission is the enricher's own registration (**N-6**). |

**Tally: 3 honoured, 8 partially, 1 honoured-in-letter-falsified-in-fact.**

---

## Summary Scorecard

| Axis | v1 | v2 | Note |
|---|---|---|---|
| Premises verified against code | 3/10 | **8/10** | Every §1а census number reproduces exactly. П1, П6, П7, П8, П9 all re-verified here. Real work. |
| Grammar correctness | 2/10 | **6/10** | `list[int]`, `[0,0]` provenance, regex-not-number all fixed. `sites`/`site` routed to the wrong branch; adopted gate unquantified. |
| Storage decision | 2/10 | **3/10** | Three options on one comparison — but the ★ column is scored on a retractability property the code refuses, and the ★ benefit measures zero. |
| Capture mechanism | 1/10 | **6/10** | Two-phase is right and is the single best change in the rebuild. Two carriers instead of one; the new seam unpriced. |
| Read boundary / security | 2/10 | **4/10** | §7а and q4 are the right shape. The containment root is the wrong root and can be `None`. |
| Contract completeness | 3/10 | **8/10** | INVARIANTS + hash spec + nested verdict is a genuine, complete answer to C-M1/C-M2/C-R9. |
| Owner questions | 1/10 | **7/10** | Non-contradictory, four-way, each reconstructed. q2(в) still lacks its measured cost. |
| Internal consistency | 3/10 | **5/10** | New contradictions introduced by the rebuild: repair-path vs storage (F-1), guarantee vs new seam (S-2), containment root vs `file_status` (F-3), one-vocabulary vs three (S-7). |

**Overall: 5 / 10 — significant rework, not a rebuild.**

The direction survives untouched for the second round: content anchor as core, resolve-on-read,
anchor outside identity. Nothing here attacks any of the three, and the two-phase restructure is a
real improvement that should be kept verbatim.

What must change before this reaches the owner, in order:

1. **Re-decide storage against the code, not against the judge's assumption.** The ring cannot be
   written by any API. Either the reader prefers top-level, or `occurrences` gains a mutation
   contract (a separate ratification), or storage goes back to (a)/(c). (F-1)
2. **Put the measured bump rate in q2.** 0 of 88 post-dedup dogfood cards. The owner is currently
   being asked to buy a new core registry for a path that has never executed. (F-2)
3. **Name the containment root as the worktree root and handle `None`.** (F-3)
4. **Collapse two carriers into one and price it as seven items, not one clause.** (S-1)
5. **Publish the basename-gate yield** (19/32, 17/40, 16/43) and re-route `sites`/`site` to the
   branch that can read them. (S-3)
6. **Correct the C2 cost cell** — the parser is not reusable. (S-4)

The pattern from round 1 has not fully cleared: the four sections that claim to be measured are
still where the defects are, and the two most load-bearing new claims — "old cards get anchors at
the next observation" and "containment identical to `file_status`" — are both assertions that
fifteen minutes of measurement contradicts.
