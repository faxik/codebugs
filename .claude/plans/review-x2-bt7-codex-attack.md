## major_risks

- **R3/R4 is impossible through the selected resolver API** | `BT-7-location-anchor.md:141-165`; `db.py:295-299,362-376`; `findings.py:278-283,1258` | A resolver must declare `anchor` in `meta_keys`, which automatically reserves it against caller-supplied add metadata. Omitting it makes the resolver’s patch invalid. `updatable_keys=("anchor",)` permits later updates but cannot make “caller-supplied wins on add” true.

- **The plan silently crosses CB-91’s unresolved trust boundary** | `BT-7-location-anchor.md:71-73,239-241`; `.codebugs/findings.db:CB-89/CB-91`; `provenance.py:335-345` | CB-89 deliberately ratified refusal of out-of-repo paths; CB-91 leaves reading/probing their owning repository open pending a trust decision. BT-7 nevertheless authorizes reading any absolute path and storing its text. That is a security/capability decision, not inherited scope.

- **Filesystem I/O occurs while holding SQLite’s write lock, with batch amplification** | `BT-7-location-anchor.md:141-149,229-236`; `db.py:546-568,430-438`; `findings.py:1265-1281,1852-1870` | `BEGIN IMMEDIATE` precedes every resolver call. `batch_add_findings` runs the resolver once per member inside one transaction, potentially rereading the same large/NFS file N times while every writer waits. The document prices “one local file,” not this actual path.

- **The owner-facing decision contradicts the recommended implementation** | `BT-7-location-anchor.md:40-42,141-161,245-252`; `findings.py:952-1001,1044-1068` | P3 and question 1 promise inheritance from the newest observation, but R3 recommends deferring exactly that. Dedup bump/reopen returns before resolvers run. The package presented for ratification therefore describes behavior the recommended first release will not have.

- **Advertised rename survival has no implementable data path** | `BT-7-location-anchor.md:103-107,113,167-173`; `provenance.py:75-95,149-164,544-552` | `_resolve_candidate` only resolves a path. `file_status` returns the rename destination embedded in prose `reason`, not as a structured `new_path`. An anchor resolver cannot open the renamed file without parsing a human diagnostic or changing provenance’s response contract.

- **`anchor_capture_skipped` cannot be measured under the proposed storage model** | `BT-7-location-anchor.md:187-189`; `findings.py:2468-2504`; `similarity.py:10-17` | The plan records neither a meta stamp nor a table row and insists on zero SQL. Existing `stats` only groups persisted finding columns. A transient resolver skip disappears, making the stated demand trigger impossible to compute.

- **R1 relies on a false characterization of RFC D5** | `BT-7-location-anchor.md:47-48,129-132,218-225`; `RFC-identity-graph-2026-08-17.md:269-283` | D5 does not establish an immutability boundary; it explicitly says fingerprint immutability versus category renames is out of scope. R1 may be reasonable for `auto:v1`, but declaring the anchor absent from future `auto:v2` pre-decides the unresolved boundary.

- **The list grammar turns location sets into giant ranges** | `BT-7-location-anchor.md:83-90,181-187`; `BT-7-location-anchor.md:97-99` | A measured producer emits `{"lines": [line_numbers]}`, yet a two-element list is interpreted as `[start,end]`. Thus `[10,100]`, meaning two reported lines, becomes a 91-line anchor. This changes meaning, expands lock-held I/O, and hashes unrelated code.

- **R5 does not define a compatible API contract** | `BT-7-location-anchor.md:167-173`; `provenance.py:584-677,794-834` | `staleness_check` currently returns a batch whose entries already own `reason`, `file_status`, and commit fields. The proposal does not say whether anchor fields replace or nest these, how singular `anchor_resolve` is selected, what its inputs are, or how renamed paths/end lines appear. Compatibility cannot be tested from this design.

- **“Manual re-anchoring” has no usable write surface** | `BT-7-location-anchor.md:134-148,167-177`; `findings.py:1948,2002-2016,3028-3067` | `update_finding(meta_update=...)` merely accepts caller JSON; it does not capture text, normalize, hash, validate versions, or calculate context. The only new command is read-only. A user must reimplement the server algorithm and can persist malformed anchors.

- **Existing and deduplicated findings cannot acquire anchors automatically** | `BT-7-location-anchor.md:158-160,206-216`; `findings.py:952-1001,1044-1068` | Existing cards have no anchors, backfill is deferred, and future observations of them bump before resolver execution. Consequently `anchor_resolve` cannot measure meaningful `lost/moved` rates on the live corpus used to justify later stages.

- **The design does not solve cross-file identity movement** | `BT-7-location-anchor.md:129-132`; `findings.py:465-480` | `auto:v1` already hashes `file`. Moving a defect to another file or renaming its file derives a different identity regardless of whether `anchor` is excluded. The design stabilizes lookup inside an existing card, not deduplication across the movement its rationale broadly invokes.

## design_smells

- **The options table omits direct diff-hunk line mapping** | `BT-7-location-anchor.md:109-125`; `provenance.py:494-509` | `(commit,file,range)` can be transformed through a single `git diff --find-renames --unified=0 commit..HEAD`, surviving edits to the anchor text that defeat A without a full history walk. Omitting it makes A appear uniquely cheap.

- **“A works on the entire population” is false by the document’s own measurements** | `BT-7-location-anchor.md:91-93,120-122` | Only regular readable files can be anchored. The measured 203 missing paths, 161 directories, 21 globs, and 5 prose values merely return unknown. Honest refusal is good, but it is not location anchoring.

- **The cost table contains unsupported numbers and rankings** | `BT-7-location-anchor.md:111-116,229-236` | “~200 lines,” “slowest,” O(history), cache needs, and cheap single-file capture are not measured. The document itself admits the lock-held cost is unknown.

- **One corpus subtotal is wrong** | `BT-7-location-anchor.md:83-90`; `/home/faxik/.cache/codebugs-identity/corpus.csv:1-3177` | Recount: 539 `lines` values = 331 strings + 206 `list[int]` + 2 dicts. Lengths 6–20 total 15, not 19; total lists are 206, not ≥210. The top-level key counts otherwise reproduce.

- **“33 multi-file prose” overstates the dogfood shape** | `BT-7-location-anchor.md:77-82`; `.codebugs/findings.db` rows such as `CB-68`, `CB-85` | Several of the 33 filename-qualified strings name only one file (`blockers.py:522`, `provenance.py:96`). They are heterogeneous filename-qualified expressions, not uniformly multi-file prose.

- **“First range” is treated as “primary” without evidence** | `BT-7-location-anchor.md:191-197` | The corpora establish ordering, not priority semantics. Selecting the first silently discards most dogfood evidence and biases resolution according to filer formatting.

- **`context_hash` is simultaneously optional and mandatory** | `BT-7-location-anchor.md:134-137,237-238` | R2 permits `context_hash: null`; §7 says it is mandatory and primary. Those produce different stored contracts and resolution outcomes.

- **The normalizer deliberately collapses semantically different Python locations** | `BT-7-location-anchor.md:175-177` | Trimming and whitespace collapse makes indented and unindented code hash alike even though indentation is semantic in Python. This directly increases ambiguity and false “current” matches.

- **P8 is not the claimed live trap for the proposed object** | `BT-7-location-anchor.md:66-69`; `findings.py:434-445` | Fingerprint normalization examines only top-level string-valued meta. `meta.anchor` is a dict, so `captured_at_commit` and other nested keys are never inspected. A test is fine, but the current code already makes the structured anchor inert.

- **The similarity precedent does not justify private provenance coupling** | `BT-7-location-anchor.md:106-107,148-149`; `similarity.py:10-17`; `findings.py:2101-2105`; `provenance.py:75` | Similarity issues no direct SQL and uses a public domain accessor. BT-7 proposes reaching into private `_resolve_candidate`; the cited precedent establishes the opposite ownership pattern.

## missing_requirements

- **Exact anchor validation** | `BT-7-location-anchor.md:134-137,175-177` | Missing bounds and invariants for `line`, `end`, hashes, `text`, unknown `v`, malformed manual updates, booleans-as-integers, reversed ranges, and lines beyond EOF.

- **Canonical hash specification** | `BT-7-location-anchor.md:113,175-177` | Hash algorithm, digest length, encoding, newline handling, multiline separators, context width N, and whether context includes the anchor are unspecified. Versioning cannot rescue an undefined v1.

- **I/O and data-exposure limits** | `BT-7-location-anchor.md:229-241` | No numeric file-size/span/text caps, timeout, encoding/binary policy, symlink policy, or authorization for absolute files. “Line seek” does not avoid scanning to a high line in a normal text file.

- **Failure semantics** | `BT-7-location-anchor.md:141-143,187-189,233-235` | Unreadable input is alternately “not an error,” a counted skip, and a queryable `resolver_errors` failure. The implementation needs a closed classification.

- **Resolution evidence** | `BT-7-location-anchor.md:167-173` | Responses need resolved end/path plus the content hash, HEAD, or filesystem revision against which the answer was computed; otherwise consumers cannot identify the snapshot represented by `line`.

- **Import/restore policy** | `findings.py:1332-1355,1436-1441,1578-1601,1604-1613` | Dynamic resolver keys are stripped by import, while restore preserves arbitrary reserved meta verbatim. The plan neither declares that asymmetry nor defines validation/degradation for restored old/unknown anchors.

- **Module exposure wiring** | `db.py:1130-1151`; `server.py:201-214`; `cli.py:103-109`; `CLAUDE.md:717-720` | A new module requires `_ensure_modules_loaded`, server mode, CLI mode, providers, schema/wire golden and behavior tests. §9 mentions only the module, surfaces, and golden.

- **Batch caching/deduplication** | `findings.py:1792-1871` | There is no requirement to capture a repeated `(resolved path, range, content state)` once per batch. Without it, resolver cost scales with batch length under one lock.

- **Confidence semantics** | `BT-7-location-anchor.md:167-171` | No formula, calibration, range, or interpretation is defined. Returning a numeric confidence without those rules adds false precision over statuses already expressing ambiguity.

## alternatives

- **Diff-hunk mapping before content fallback** | `provenance.py:494-509`; `BT-7-location-anchor.md:113-116` | Store `(commit,file,start,end)` and map it through one rename-aware diff; use content/context only when a hunk makes the mapping ambiguous. This fills the missing middle between A and history-walk C.

- **Precompute capture outside `db.txn`** | `RFC-identity-graph-2026-08-17.md:202-210`; `db.py:546-568` | Follow RFC S2’s two-phase pattern: read/hash files before `BEGIN IMMEDIATE`, then let an in-transaction resolver validate and stamp the precomputed object.

- **Structured coordinates as the primary input** | `BT-7-location-anchor.md:199-204`; `findings.py:3311-3317` | Add explicit `line`/`end` arguments and keep legacy `meta.lines` parsing as compatibility fallback. This removes ambiguous list/range semantics and makes CLI conflict handling explicit.

- **Occurrence-level capture plus derived latest anchor** | `findings.py:483-525,624-637`; `provenance.py:555-581` | Capture an anchor for every observation outside the lock, store it in the occurrence entry, and resolve the newest usable one on read. This matches CB-53 without mutating authored top-level meta or inventing a bump-only promotion seam.

## confidence

0.98 — I could not reproduce the historical 34.5 ms timing or the undocumented classifier that split 47 irregular `lines` strings into exactly “41 file:line + 6 prose”; all code paths, database counts, CSV key/type counts, commit `f0b4010`, and cited RFC/card contracts were checked read-only.