## major_risks

- **The mandatory-fix gate still fails** | `.claude/plans/review-x2-bt7-judge-verdict.md:121-198`; `.claude/plans/BT-7-location-anchor.md:122-296` | Fixes #6, #7 and #10 are substantially honored; the rest are partial: #1 compares a crippled table option, #2 lacks an implementable carrier, #4 has an unsafe containment rule and no numeric caps, #5 omits a literal grammar/precedence, #8 loses capture failures, #9 remains underspecified, #11 binds some reads to the wrong checkout, and #12 omits promised surfaces from the cut.

- **Default containment still permits arbitrary-file reads through a final symlink** | `BT-7-location-anchor.md:252-259`; `provenance.py:75-95,335-345,439-458` | `_resolve_candidate` resolves only the parent and deliberately preserves the final symlink. `commonpath` therefore accepts `<repo>/link -> /etc/passwd`; `os.stat` and a later `open` follow it. That asymmetry is safe for git blob classification, not for reading and persisting bytes. Mandatory fix #4’s security boundary is not honored.

- **The reserved `loc` object has no implementable pre-lock carrier** | `BT-7-location-anchor.md:156-178`; `findings.py:1256-1265`; `db.py:333-345`; `findings.py:1044-1068` | Registration reserves `loc` before enrichment, so placing it in caller/meta input is refused. The document merely says the object “rides in observation”; it defines neither a separate internal field nor signatures carrying it into `_add_one`, the insert resolver and `_occurrence_entry`. Mandatory fix #2 asked for that named carrier, not just the phrase “observation enricher”.

- **R1’s structural identity guarantee is invalidated by the new pre-lock seam** | `BT-7-location-anchor.md:68-74,140-142,156-165`; `findings.py:936-951` | Fingerprint-before-resolver proves only that existing pre-add resolvers cannot affect identity. The proposed generic enricher runs earlier than fingerprint derivation; unless its contract forbids mutation of identity inputs/meta and uses an isolated carrier, it can change `auto:v1`. No such restriction is specified.

- **Capture failures remain unmeasurable despite the document claiming the opposite** | `BT-7-location-anchor.md:183-185,222-228,233-240` | Failure produces no `loc`; R5 defines missing anchors as `anchor: null`, while §4’s reasons are not persisted anywhere. A null cannot distinguish legacy/no-location from `binary`, `out_of_repo`, `no_grammar`, or `ambiguous_multisite`, and it cannot contribute to the promised `lost` rate. Mandatory fix #8 and §5’s decision procedure still fail.

- **The tombstone and manual-repair semantics do not work with newest-ring-first reads** | `BT-7-location-anchor.md:151-165,170-172`; `findings.py:2002-2016` | `meta.loc=null` changes only the top-level fallback; the reader first consumes usable ring anchors, so existing ring anchors remain active and later observations add more. Likewise, manual top-level re-anchoring is ignored whenever the ring already contains a usable anchor. The only declared retraction and repair paths are ineffective for the recommended storage model.

- **C2 has no valid mapping base for dirty or caller-supplied snapshots** | `BT-7-location-anchor.md:118-120,146-159,193-202`; `findings.py:2891-2893,2977-2979` | Capture hashes working-tree bytes, while C2 diffs a commit tree to `HEAD`. The design does not require those bytes to equal `captured_at_commit`, and the existing API permits a caller-supplied commit. Dirty-at-capture content therefore has coordinates unrelated to C2’s old tree, enabling confidently wrong remaps.

- **C2 does not cover uncommitted current edits** | `BT-7-location-anchor.md:118-120,180-189`; `provenance.py:494-509` | `<captured>..HEAD` excludes the working-tree delta, although resolution explicitly targets the working tree and records its `mtime_ns/size`. If the anchor text was edited but not committed, C2 sees no edit and A may fail. The claim that C2 closes A’s “edited anchor lines” hole is false without a second `HEAD → working tree` mapping.

- **The default root can resolve anchors against main instead of the active linked worktree** | `BT-7-location-anchor.md:186-191`; `db.py:1019-1060`; `CLAUDE.md:640-643`; `provenance.py:60-95` | `describe_root()` returns the tracker/database root; worktree discovery deliberately binds linked worktrees to the main checkout’s database. Using that root as the code root reads main’s files, not the branch under review. Explicit `project_dir` helps `anchor_resolve`, but the proposed `get(resolve_anchor=True)` and `staleness_check(anchor=True)` expose no equivalent argument.

- **The “fully specified” v1 normalizer is not reproducible** | `BT-7-location-anchor.md:193-202,252-257`; `findings.py:465-480` | Context width `N` and every cap remain unnamed; “indent → depth token” does not define tabs/mixed indentation or token bytes; `json.dumps` encoding/separators are unstated despite the existing fingerprint explicitly choosing `ensure_ascii=False`. Persisted v1 hashes cannot be independently reimplemented. Mandatory fix #9 is not honored.

- **Manual updates can persist arbitrary malformed or spoofed anchors** | `BT-7-location-anchor.md:146-154,170-178`; `db.py:295-308`; `findings.py:1946-1948,2012-2016` | `updatable_keys` only exempts `loc` from reservation. `update_finding` then performs an unrestricted `dict.update`; it never invokes the proposed invariant validator. Callers can store invalid hashes, bool lines, impossible ranges or forged commits—the exact malformed-manual-update gap the first verdict required v2 to close.

- **The grammar has no precedence for real rows carrying multiple sibling keys** | `BT-7-location-anchor.md:204-220`; `.codebugs/findings.db (CB-80)`; `../autosorter/.codebugs/findings.db (CB-1855)` | Current data contains `lines+sites`, `lines+function`, `line+function`, and `lines+line+function` combinations—28 rows in the current autosorter tracker plus CB-80 locally. The document defines branches but not which key wins, whether sites merge, or whether disagreement becomes ambiguous. Capture is input-order/implementation-choice dependent.

- **`sites_dropped` cannot measure the dominant rejected multi-site population** | `BT-7-location-anchor.md:81-85,216-230` | The counter exists only inside a successfully captured anchor object. Multi-filename inputs are skipped as `ambiguous_multisite`, producing no object and therefore no counter. The proposed trigger systematically omits the very 30/32 multi-site dogfood rows used to justify it.

- **Promised read/write surfaces are absent from the implementation cut** | `BT-7-location-anchor.md:180-191,286-296` | R5 promises `get(resolve_anchor=True)` and refers to CLI/MCP resolution, but T-b lists only `anchor_resolve`, `staleness_check(anchor=)`, `anchor_recapture`, and a golden. No CLI commands, CLI output/exit contract, or `get` change are scheduled. Mandatory fix #12 is incomplete.

## design_smells

- **The storage comparison biases the table option** | `BT-7-location-anchor.md:124-136` | A table is labeled insert-only and in need of an UPDATE path, but it can append one anchor per observation through exactly the same enrichment seam as the ring. Existing cards would then acquire anchors on re-observation without backfill. Storage representation and capture population are incorrectly coupled.

- **The missing obvious option is `locs:[…]` per observation** | `BT-7-location-anchor.md:124-136,222-230` | A bounded array inside each occurrence supports N locations, inherits ring import/restore semantics, and needs no schema migration. The table is falsely presented as the only multi-anchor option even though multi-site input is the measured norm.

- **“Freshness/history for free” hides ring loss and byte cost** | `BT-7-location-anchor.md:126-131,163-169`; `findings.py:392-397,629-637` | The ring keeps only first 10 + last 10 and drops the middle. Adding text and hashes to every retained entry is not free, and discarded observations make the supposed location history incomplete.

- **`MAX_TEXT_BYTES` is enforced with a character count** | `BT-7-location-anchor.md:150-152` | `len(text)` counts Python code points, not encoded bytes. The stated invariant does not enforce its named byte cap, especially with non-ASCII source.

- **A per-occurrence anchor is not explicitly bound to its sibling occurrence file** | `BT-7-location-anchor.md:144-169`; `findings.py:512-525` | Occurrence entries carry their own `file`, and supplied fingerprints can legally match observations independently of `auto:v1`’s file input. The `loc` object contains no path, and the reader contract never says to resolve ring `loc` against that same entry’s `file` rather than the row’s frozen file.

- **C2 reuses only a rename-record parser, not a hunk-remapping implementation** | `BT-7-location-anchor.md:118-120,288-291`; `provenance.py:167-196,483-550` | Provenance parses NUL-separated `--name-status` rename triples. Unified-diff hunk parsing, overlapping-span behavior and modified-hunk mapping are all new. Calling the parser precedent sufficient materially understates C2’s complexity.

- **The CB-65 “shared locus is a consumer” statement is unsupported** | `BT-7-location-anchor.md:242-246`; `RFC-identity-graph-2026-08-17.md:84-113,202-257` | RFC S0 is adjudication/evaluation and S2 is candidate scoring; neither defines a consumer of symbolic anchors or a shared-locus interface. The live CB-65 evidence used shared locus as one candidate-generation instrument, not as a downstream consumer.

- **The historical measurements are not reproducible from the document** | `BT-7-location-anchor.md:79-106` | No query/script, snapshot hash or artifact path is supplied. The live databases now contain 129 and 3296 rows rather than 128 and 3176; historical list counts remain plausible, but a reviewer cannot reconstruct the claimed snapshot.

- **The batch benchmark is measurement without a gate** | `BT-7-location-anchor.md:250-251,286-296` | T-a must measure a cold 100-member batch but specifies no maximum duration, read budget, regression baseline or refusal threshold. Any result can therefore be declared acceptable after the fact.

- **Failure vocabularies are split rather than closed** | `BT-7-location-anchor.md:147-152,183-185,222-228` | Capture reasons, read statuses, `unsupported_anchor_version`, and `retracted` appear in different clauses with no total status×reason matrix. This does not meet the prior verdict’s “one closed classification at all three sites”.

## missing_requirements

- **Safe-open policy** | `provenance.py:75-95`; `BT-7-location-anchor.md:252-259` | Require `lstat`/`O_NOFOLLOW` or an fd-relative open, `fstat(S_ISREG)`, containment of the opened object, and race handling. Reusing `file_status` containment is insufficient for byte reads.

- **Exact enrichment seam contract** | `findings.py:1192-1282,1792-1871`; `BT-7-location-anchor.md:156-165` | Specify signatures, isolated carrier field, order relative to normalization/fingerprinting, caching key, failure type, and behavior for explicit IDs, `annotate=False`, raw library callers, CLI, MCP and batch members.

- **Formal grammar and priority** | `BT-7-location-anchor.md:204-220` | Publish actual regexes; whitespace/sign/zero/descending-range rules; recursive `sites` list handling; sibling-key precedence; first-invalid/next-valid behavior; EOF policy; and exact `sites_dropped` counting. “NAME.ext:DIGITS or int-like” is a description, not a grammar.

- **Resolution algorithm** | `BT-7-location-anchor.md:114-120,180-189` | Define local-search radius, global fallback, how context ranks duplicates, tie behavior, range matching, hash-collision handling, and the exact predicates producing `current`, `moved`, `lost`, `ambiguous`, or `unknown`.

- **Snapshot integrity** | `BT-7-location-anchor.md:183-189` | `head+path+mtime_ns+size` is not a content identity and can mismatch bytes read during a concurrent write. Require one opened-file snapshot and return its digest/fstat evidence, or detect change and answer `unknown`.

- **Capture-commit invariant** | `BT-7-location-anchor.md:146-159,193-202` | State whether `captured_at_commit` is capture-time HEAD or caller provenance, verify that the captured blob/range exists in it before C2, and define dirty-tree degradation.

- **Complete MCP/CLI contracts** | `BT-7-location-anchor.md:180-191,286-296`; `provenance.py:587-680,800-837` | Define outer `anchor_resolve` response, filter/default semantics, missing-ID behavior, per-record fields, CLI commands and output, `project_dir` availability on every resolving surface, and golden/behavioral tests.

- **Shared validation at every ingress** | `findings.py:1332-1355,1570-1601,1946-2016` | The same validator must cover resolver output, manual updates, recapture, ring reads and restore-preserved values. Unknown versions may degrade on read, but malformed ordinary updates should be refused before write.

- **Numeric resource limits and EOF behavior** | `BT-7-location-anchor.md:149-152,252-257` | Ratification needs actual values for file bytes read, span lines, persisted text bytes and context width, plus behavior for a line beyond EOF. Deferring all numbers to T-a leaves the storage and security contract undecided.

- **Backfill and demand metrics** | `BT-7-location-anchor.md:233-240,286-296` | Define which rows are eligible, how `git show` paths follow renames, how failed captures are reported, and how aggregate lost/capture-failure/multisite rates are computed without polluting stored findings.

## alternatives

- **Bounded multi-anchor occurrence object** | `BT-7-location-anchor.md:124-136,222-230` | Store `occurrences[*].locs` with a per-observation count/byte cap; derive the primary for compatibility while preserving the measured multi-site evidence.

- **Append-only anchor table** | `relations.py:1-23`; `BT-7-location-anchor.md:124-136` | Insert one or many anchors per observation, including explicit capture-failure rows. This makes failure rates and hash queries measurable without pretending the table requires top-level updates.

- **Opaque pre-lock annotation registry** | `db.py:280-330`; `findings.py:936-1068` | Add an enricher registry returning isolated, declared internal annotations that are never merged into fingerprint inputs; the in-transaction consumer validates and routes them to insert or bump storage.

- **Structured `line`/`end`/`sites` inputs first** | `findings.py:2882-2895,2995-3035,3339-3361` | Add typed coordinates to domain, MCP and CLI APIs, reject conflicts with legacy meta, and retain the legacy grammar only as an explicitly lossy compatibility fallback.

- **Dirty-aware mapping cascade** | `provenance.py:483-550`; `BT-7-location-anchor.md:118-120` | Map captured commit → HEAD, then HEAD → working tree, and accept a remap only when the captured content/coordinates are proven to correspond to the base tree; otherwise fall back to content search.

- **Read-time legacy capture/backfill report** | `BT-7-location-anchor.md:233-240` | Before persisting anything, run a dry report using stored commit plus `git show`, returning proposed anchors, capture failures and multi-site counts. It supplies the missing decision metrics without mutating live cards.

## confidence

0.98 — I could not independently reconstruct the historical 3176-row snapshot or the 23.7/34.5 ms timing runs; current code paths, live database shapes, CB-65/89/91/93 contracts, and all cited implementation behavior were verified read-only.