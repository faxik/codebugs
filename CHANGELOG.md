# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- **Re-reporting a known defect no longer fails over a category typo, and the
  occurrence ring now records each observation's category (CB-113).** The category
  mint gate (CB-60) used to run before the deduplication branch was known, so an
  observation carrying a supplied fingerprint that matched an existing live card —
  but a near-miss or unknown category spelling and no `new_category` flag — was
  refused outright, and the occurrence (count bump, ring evidence, regression
  reopen) was lost over a spelling dispute about a row that already exists. The
  gate now runs only when the observation would **create** a row: a fingerprint
  match on a live card bumps it, and a match on a `fixed` card reopens it,
  regardless of the observed category spelling. Genuinely new findings are gated
  exactly as before — an unknown category is still refused with a hint unless
  `new_category` is passed, a near-miss still names the canonical spelling, and a
  recurrence of a `wont_fix`/`not_a_bug` card (which files a new row) is still
  gated. Every ring entry now carries a `category` key (unconditionally — `""` is
  a legal category), so a merge across category spellings stays reconstructable
  from the ring alone; the stored `category` column is never rewritten by a bump.
  Entries written before this change simply lack the key. CSV import stays
  ungated (`gate_category=False`, its single sanctioned call site, pinned by an
  AST ratchet) — an import is not an observation, so a peer's category lands
  verbatim.
- **Re-reporting a known defect with new tags now adds them to the card (BT-4).** Since
  findings gained an identity function, a second report of the same defect bumps the
  existing row — but the bump left the `tags` column frozen at the first report, so a
  tag arriving with a later observation survived only inside the `meta.occurrences`
  ring. The visible cost was on the tag-filtering read paths: `query(tag=)` and
  `tag-report` never saw the re-observed tag.

  A bump now stores the **union** of (stored, observed) tags: exact string equality
  (`Tag` and `tag` are different tags — no case folding), first-encountered order with
  stored tags before observed ones, duplicates dropped.

  **A regression reopen unions too** — a regression is an observation, so a
  `fixed-in-1.2` tag carried by the re-report lands on the reopened card.

  **Importing does not promote foreign tags.** `import-csv` records the occurrence —
  the ring still carries the peer's tags — but the local column stays this tracker's
  own: an import is not an observation. That opt-out has exactly one call site
  (`import_findings`), pinned by an AST ratchet, and is deliberately not exposed on
  the public add surface.

  **Corruption classification moved for stored tags.** The union strict-parses the
  stored column before writing, so a bump over malformed stored tags now fails
  pre-write with nothing landed (it used to commit the bump and then raise
  `PostCommitCorruptionError` at serialization). The import path neither reads nor
  writes the column, so an import's live-hit on a corrupt row still lands.

  **Tag removal stays a full replace for now** — `update(tags=)` overwrites the whole
  list, so a hand-removed tag returns with the next observation that carries it. The
  sub-decision (a cap, tombstones, or a `finding_tags` table) is open with the owner.
- **Re-reporting a known defect as more severe now raises its severity (CB-52).** Since
  findings gained an identity function, a second report of the same defect no longer
  creates a row — it bumps the existing one. But the bump wrote only the occurrence
  count and timestamps, so **every other column stayed frozen at the first report**. A
  card filed `low` and later observed as `critical` stayed `low`, and the newest
  assessment survived only inside the `meta.occurrences` ring. The visible cost was on
  the main read path: `query(severity="critical")` did not return it, so the card was
  effectively invisible to anyone looking for critical work.

  A bump now writes the **more severe** of (stored, observed).

  **Escalation is one-way.** Re-observing a `critical` card at `low` leaves it
  `critical` — a re-observation can raise severity, never lower it. Use `update` to
  downgrade deliberately.

  **Importing does not re-rate.** `import-csv` records the occurrence but leaves the
  local severity alone: a peer's tracker calling their copy `critical` is their
  assessment of their repository, not evidence about yours. This matches the existing
  rule that an import is not an observation.

  **A regression reopen escalates too** — a fixed card that comes back worse than it
  went reopens at the new severity.

  Not changed: which milestone stream a finding sits in. Routing is still decided once,
  at filing time, and re-evaluating it on a severity change is tracked separately as
  CB-35.
- **`codebugs reqs-import` no longer reports success when the database gives out
  mid-import (CB-99).** The per-row insert was guarded by `except sqlite3.Error` — the
  whole SQLite exception tree — so a full disk or an I/O error part-way through was
  counted as a *malformed row* and the command printed `Imported 0 requirements,
  skipped 47.` and exited **0**. Measured with a simulated `SQLITE_FULL`:
  `{'imported': 0, 'skipped': 2}`, no error at all.

  The guard now catches only `IntegrityError` — constraint violations, i.e. genuinely
  "this row is wrong". Anything about the statement or the environment propagates, so
  the command fails loudly instead of quietly.

  **One consequence worth knowing.** The commit happens at the end of the import, so a
  failure part-way now leaves **nothing** written rather than a partial import reported
  as success.

  **A nonzero `skipped` is still ordinary and does not mean this fix misfired** — it
  counts malformed table rows (a row with fewer than four columns), which is what it
  always counted and what you should read it as.
- **A tracker you cannot write to now says so, instead of crashing or telling you to
  run `init` (CB-86).** On a read-only mount, a tracker owned by another user, a
  directory the process cannot write, or a full disk, `codebugs` behaved in one of two
  wrong ways depending on how you had pointed it at the tracker:

  - discovered by walking up from the current directory → a raw Python traceback;
  - named with `--tracker-root` or `$CODEBUGS_ROOT` → the clean but **wrong** message
    `no readable findings.db at … ; run codebugs init for that project`, which advises
    you to create a tracker that already exists and which `init` would refuse anyway.

  Both now print one line naming the real problem — `cannot open findings.db at … for
  writing (…); check permissions on the file and its directory, and free disk space` —
  and exit 1.

  **A genuinely missing tracker still says `run codebugs init`.** SQLite reports the
  same error code for "the file is not there" and "the file is there and you may not
  open it", so the two are told apart by whether the file exists. One known gap: if the
  *parent directory* is unreadable, the file cannot be seen either, and the message
  falls back to "not found" — which is what it said before too.

  Unaffected on purpose: a failure that happens *after* the tracker opened
  successfully — a disk that fills mid-command — still ends in a traceback. That is
  deliberate; the traceback is what tells you the failure came after work had begun.

### Changed
- **A closed pipe now ends a `codebugs` command at exit 141, silently, instead of
  reporting a committed write as a failure (CB-78).** Piping any verb into a reader
  that goes away — `| head`, `| true`, a `gzip` that dies — used to print a Python
  `BrokenPipeError` traceback and exit **1**, or, under the default block buffering,
  `Exception ignored on flushing sys.stdout` and exit **120**. In both cases the
  command's work had **already committed**: `codebugs add … | true` filed the finding
  and then reported failure.

  `codebugs` now restores the POSIX default for `SIGPIPE`, so it dies by signal like
  any other Unix filter (`yes | head` does the same). **Exit 141 means "the reader of
  my stdout went away", and is deliberately distinguishable from exit 1, "the command
  failed"** — that is why the process does not simply exit 0: a truncated
  `codebugs export-csv /dev/stdout | gzip > backup.gz` must never look like a
  successful backup.

  **What this costs you, stated rather than left to be discovered.** Where a
  broken pipe previously produced a readable line, it is now silent. Concretely,
  `codebugs export-csv /dev/stdout` and `codebugs reqs-export` into a dead reader used
  to print `codebugs: [Errno 32] Broken pipe` and exit 1; they now exit 141 with empty
  stderr. `codebugs --help | head -0` exits 141 rather than 0. Both outcomes are
  non-zero, so no `set -e` script changes behaviour, but a script that matched on
  stderr text will see nothing.

  It is only observable when the reader closes **without draining** the output (at any
  size), or when un-drained output exceeds the pipe buffer (64 KB on Linux). A one-line
  `codebugs add` piped to `head -1` is unaffected, because `head` reads it first.

  **If you installed `codebugs` before this release, re-install it** — `pipx reinstall
  codebugs`, or `pip install -e .` in a checkout. The console script is generated at
  install time and the old one bypasses the new entry point, so the fix will not reach
  you otherwise.

### Added
- **`codebugs restore-csv <file>` — put a backup back exactly as it was (CB-97).**
  `import-csv` folds someone else's findings into yours and gives them fresh ids;
  `restore-csv` states that these rows *are* the tracker, writing ids, statuses,
  occurrence counts, tags, fingerprints and timestamps verbatim. Until now
  exporting and re-importing renumbered every card and reset every status to
  `open`, so a CSV export was a report rather than a backup.

  It **refuses rather than merges**: every row needs an id, no id may already
  exist locally, and no id may repeat in the file. A refusal names the offending
  ids and writes nothing — the whole restore is one transaction.

  **What a restore cannot bring back, said plainly:** milestone items and their
  audit history. They are a projection, they are not in the CSV, and inventing
  them would fabricate a triage history that never happened. The command prints
  this on stderr rather than leaving you to notice.

### Changed
- **`export-csv` now carries `reported_at_commit` and `reported_at_ref`, and no
  longer stops at 100 000 rows.** Without the provenance columns a restored
  tracker could not answer `staleness_check` for any card; the row cap silently
  truncated larger trackers on the one path where losing rows costs most. Both
  are additive — older CSVs still import.
- **CSV import no longer drops another tracker's findings, and no longer resurrects
  your own decided ones (CB-51).** Every import rule had accumulated inline in the
  CLI handler, which called `add_finding` once per row. Four defects fell out of
  that, all measured before the fix:
  - a foreign row whose text matched a local **`fixed`** card **reopened** it —
    an import is a statement about someone else's tracker and must not resurrect
    a decision here;
  - the "already present" guard compared **bare ids**, and every tracker numbers
    `CB-1, CB-2, …`, so importing a peer's export dropped every row whose number
    happened to be taken locally — silently, counted as "already present";
  - the exported `tags` column was parsed by nobody;
  - and because `export-csv` orders by **severity** rather than id, restoring a
    backup into an **empty** tracker lost rows: the id allocator handed out ids
    that later rows of the same file still named. Three rows out, two back,
    exit 0.

  Imports now compare content as well as id, so a colliding foreign row lands with
  a fresh local id and records its origin in `meta.imported_id`; a re-import of
  your own export is still a no-op.

  **Not yet covered:** a faithful *restore* — preserving id, status and occurrence
  counts — needs a different mechanism and is tracked separately. Exporting and
  re-importing still renumbers cards and resets their status to `open`.
- **A failed import now leaves nothing behind (CB-77).** The whole import runs in
  one transaction and the file is read before it opens, so a read failure part-way
  through no longer leaves earlier rows committed. A failed import reports that
  nothing landed rather than printing a count.

- **`staleness_check` now resolves a finding's `file` against the repository
  root, as `findings.py` has always documented it (CB-93).** Every staleness
  operation previously resolved it against the *process* cwd, so the documented
  root-relative spelling was the one that failed: from a subdirectory
  `file_status(file="pkg/mod.py")` reported `unknown`/`not_in_commit` for a file
  that had genuinely changed. This is reachable on the ordinary path, not just
  from a shell — the MCP tool passes `project_dir=None` and `db.connect()`'s
  walk-up permits the server to start anywhere at or below the root, so a
  long-lived server launched one directory down misreported the whole tracker.

  **This is a behaviour change on a documented surface, stated plainly.** A
  `file` value written relative to a *subdirectory* used to resolve and now does
  not; it degrades to `unknown` with a reason rather than to a confident wrong
  verdict. The changed population was measured before the decision was taken:
  across 3,307 findings in two real trackers, **zero** used a
  subdirectory-relative value.

### Fixed
- **A renamed file could be reported as `deleted` — a confident claim that a
  present file is gone (CB-92).** The rename probe split `git diff
  --name-status` output on TAB and compared it against the caller's raw
  spelling, which failed four ways, each ending at the same unconditional
  `deleted`: git C-quotes non-ASCII paths by default (`"src/\303\244.py"`, with
  the quotes); a TAB or newline in a name broke the split; git canonicalises the
  name it prints, so `./src/x.py` never matched; and `git diff` prints
  root-relative paths regardless of cwd, so no nested path matched from a
  subdirectory. The probe now reads NUL-delimited records (`-z`) and compares
  against the canonical root-relative path, which closes all four.

  This was the fourth route into this module's most-repeated false positive,
  after CB-79, CB-85 and CB-88 — each a different unasked question ending at the
  same line.
- **Staleness answers no longer depend on the ambient locale.** Every git reader in
  `provenance.py` and `db.git_rev_parse` decoded with `text=True`, i.e. with
  `locale.getpreferredencoding()`. Under `LC_CTYPE=C` that is ASCII, and two
  things broke: `git log --oneline` raised `UnicodeDecodeError` on an ordinary
  commit subject such as `fix café`, and the rename comparison decoded git's
  UTF-8 path bytes into surrogates that could never match the stored path — so a
  renamed file reported a confident `deleted` on one machine and `renamed` on
  another. All readers now pin `encoding="utf-8"` explicitly.
- **A relative `GIT_DIR` / `GIT_WORK_TREE` is now refused instead of answered
  wrongly.** Git resolves those against the process cwd, and staleness probes run
  from the worktree root, so a relative value silently named a different
  repository — measured, a genuinely modified file reported `current`. It now
  returns `unknown` naming the variable. Absolute values are unaffected.
- **A non-UTF-8 filename no longer aborts a whole staleness batch.** Suppressing
  git's C-quoting means raw path bytes arrive, and strict decoding raised
  `UnicodeDecodeError` — a `ValueError`, so it escaped the module's
  `(SubprocessError, OSError)` guards entirely. The rename probe takes no
  pathspec, so one undecodable rename anywhere in the range would have killed
  every finding in a `staleness_check`, including plain-ASCII ones. Both
  NUL-reading probes now decode with `surrogateescape`. Found by adversarial
  review of the CB-92 fix, before it shipped.

### Added
- **`relations.py` — typed, retractable relations between findings.** Callers
  had been recording relations in ad-hoc JSON `meta` keys for months: measured
  over the 3,176-row reference corpus, **164 distinct key names for roughly five
  concepts** (five spellings of "related", four of "sibling", three of
  "parent"), carrying 837 edges across 76 yielding keys — and the share of new
  cards minting such a key went from 0.2% in March to ~25% and holding. That
  substrate answers no question and forgets nothing: `meta` writes are
  merge-only, so a key can be overwritten but never removed.

  `finding_relations` stores one edge per row over a six-term vocabulary
  (`duplicate_of`, `split_from`, `follow_up_of`, `found_during`,
  `distinct_from`, `related_to`), enforced by a DB-level `CHECK` rather than in
  application code. Three tools and three CLI verbs: `relations_relate`,
  `relations_unrelate`, `relations_query` (which, filtered to `distinct_from`,
  is the active-suppressions review).

  - **Orientation is data, not noise.** `distinct_from` and `related_to` are
    symmetric and stored canonically, so one edge exists per pair and no reader
    searches both directions. The rest are directed, and **`duplicate_of` names
    loser → survivor** — canonicalising it lexicographically would swap which
    card survives, which it does in *all three* real `duplicate_of` facts on the
    reference corpus (`CB-878→CB-877`, `CB-2946→CB-2935`, `CB-2251→CB-2227`).
  - **Endpoints are validated for EXISTENCE, not liveness**, in the application
    layer — `db._open` never enables `PRAGMA foreign_keys` and `findings.py`'s
    legacy status migration toggles it OFF/ON, so a declared FK would be
    decorative. Liveness would have been worse than useless: 62.8% of the
    corpus's edges point at closed cards, and a live card citing a closed one is
    precisely the case prose cannot resolve.
  - **Retraction is a tombstone, never a DELETE**, and the partial unique index
    (`WHERE retracted_at IS NULL`) then permits the pair again. A paired
    nullability `CHECK` makes a tombstone without an actor unrepresentable. The
    dangerous write here is a wrong `distinct_from`: it suppresses a pair from
    every future discovery path, and a suppression that should not exist is
    invisible by construction.
  - **Contradiction guards** refuse a `duplicate_of` against a live
    `distinct_from` on the same pair (and the reverse), and refuse a reciprocal
    `duplicate_of` — both cards cannot be the loser. A retracted edge asserts
    nothing and therefore vetoes nothing.
  - Three terms are refused with a pointer rather than accepted:
    `recurrence_of` (core-owned, guarded by `_RESERVED_META_KEYS` against the
    spoofing attack its comment names), `blocked_by` (the blockers module owns
    finding→finding blocking, with lifecycle semantics this table lacks), and
    `similar_to` (annotator-owned, an advisory snapshot rather than a fact).

  Note `distinct_from` is **inert** until a consumer reads it: `similarity.py`
  groups via DSU over category blocks and does not consult this table. Stated
  rather than implied. Migration of the legacy `meta` keys is deliberately not
  included — it is a separate, not-yet-implementable piece of work whose open
  defects are recorded in `.claude/plans/PLAN-relations-migration-2026-08-18.md`.

- **`grouping.py` — the three grouping axes the tracker stored but could not
  query.** Similarity is the axis codebugs already had, and on the 3206-row
  reference corpus it is the weakest: exactly one similarity family (two cards)
  exists across 1093 open cards, measured three times. The axes that actually
  carve that backlog into work units had no read surface at all, so callers
  either did without or reimplemented tracker semantics themselves.

  - `citation_report` — connected components of the hand-written CB-id
    reference graph, over the description and every string in `meta` (which is
    where the notes history lives). Every edge carries the field it came from
    and the quoted window around its first mention: an edge nobody can read is
    not evidence. Raw components do **not** decompose a real corpus — the
    largest holds 345 of 546 linked open cards, because a few much-cited
    landmark cards glue every neighbourhood together — so a node whose degree
    exceeds `hub_degree` (default 3) stops transmitting connectivity and is
    reported as a terminal ANCHOR with its citers instead. That turns the one
    hairball into 117 components whose largest is 11 cards. The default is
    chosen on the outcome and not on the degree histogram, whose elbow is at 4
    and yields a 55-card largest component — see the sweep table at
    `DEFAULT_HUB_DEGREE`. References out of the population are counted as
    dangling, never dropped.
  - `tag_report` — tag counts, co-occurrence with Jaccard beside the raw count,
    and near-duplicate taxonomy strings folded across tags AND categories in one
    namespace (`process_improvement` and `process-improvement` are two live
    categories in the same tracker, and every count over either is wrong by the
    size of the other).
  - `filing_report` — split lineage TRAVERSED, not grouped: `A → B → C` is one
    lineage with depths, links resolve against every card in the tracker so a
    `fixed` middle card does not sever the chain, meta cycles terminate and are
    flagged, and a lineage value that names no card (`"autosorter prod bug
    d7ec2391"` is real) is reported unresolved rather than silently dropped.
    Plus `sprint` / `plan` filing-event groups. `parent` joins the documented
    `split_from` / `split_children` keys — it is the same relation with 20x the
    rows on the reference corpus.

  Same contract as `similarity.py`: zero SQL in the module, read-only, and no
  link inferred that a human did not write. Rows arrive through the new
  `findings.grouping_candidates` accessor — a sibling of `similarity_candidates`
  rather than a widening of it, since it carries `tags_json` and the two feed
  different algorithms.
- **`findings.parse_meta` / `findings.parse_tags`** — the tolerant parses for the
  two JSON columns findings owns, public so every reader of a raw blob degrades
  the same way. `parse_meta` was `similarity._parse_meta`, already documented as
  "the ONE place"; it now actually is one.

### Changed
- **Union-find has one copy.** `similarity._DSU` moved to `grouping.DSU`, the
  module whose subject is components over an edge set; families are that same
  primitive over score edges. Two copies were one edit away from the two
  surfaces disagreeing about what a component is.

### Fixed
- **A benchmark CSV no longer kills `bench-import` with a raw
  `sqlite3.IntegrityError` traceback** (CB-81). Validation used to be
  interleaved with the writes: the run row went in, then each cell was checked
  inside the loop that inserted it, so the database's own constraints were the
  only thing checking the payload — and a constraint failure is an
  `IntegrityError`, which no error arm in the CLI handles. Three ordinary
  payloads reproduced it: duplicate row labels, a duplicate header, and `nan`
  (SQLite stores NaN as NULL, so it tripped `NOT NULL`). A malformed CSV did the
  same through `_csv.Error`, which is not a `ValueError` either.

  Every payload fault is now decided **before the first INSERT** and reported as
  an ordinary input error naming the row and column, and the writes — with the
  run-id read that feeds them — run inside a single transaction. Nothing was
  being left behind on the CLI or MCP paths (both discard the connection on
  failure), but the atomicity was accidental rather than intended, and the
  run-id read was an unlocked read-modify-write that could collide between two
  concurrent imports.

  **Behaviour change:** a metric value of `inf`, `-inf`, `Infinity`, or a literal
  that overflows to infinity such as `1e400` imported silently before and is now
  refused — a non-finite measurement is not a measurement. `nan` was already
  refused, just in constraint vocabulary and after a write. This applies to
  metric *values* only; the `meta` field's NaN/Infinity policy is unchanged.

  Two things that still import exactly as before, and are pinned so they stay
  that way: a repeated row label whose cells are disjoint, and a duplicate
  header column whose cells are blank. The duplicate check is on
  `(row label, metric)` pairs, which is the existing uniqueness rule applied
  earlier, so it refuses nothing that used to work.

- **MCP tool descriptions no longer depend on which Python built the server**
  (CB-73). The SDK reads `Tool.description` from `__doc__`, and CPython 3.13
  dedents docstrings at compile time while 3.11 and 3.12 do not — so on the
  older interpreters `requires-python` promises to support, clients received the
  source indentation. That is not cosmetic: MCP clients render descriptions as
  Markdown, and CommonMark treats a 4-space-indented line following a blank line
  as an **indented code block**, so the entire prose body of most tools rendered
  monospaced as code.

  Measured across both interpreters: **64 of 68 descriptions differed** between a
  3.12- and a 3.13-hosted server, and **61 of 68** contained the code-block
  pattern. Both are now 0, and 3.13 output is byte-identical before and after —
  which is why the wire golden does not move.

  Normalized once at registration through the SDK's public `description=`
  parameter. No private API, no `__doc__` mutation, and an explicit description
  passed by a caller still wins.

- **A file whose existence could not be checked is no longer reported as
  `deleted`** (CB-85). `staleness_check` used `os.path.isfile`, which returns
  `False` for *any* stat failure — an unreadable parent directory, a symlink
  loop, a stale network handle — not only for "the file is absent". That `False`
  skipped the `modified` branch and the code below then stated `deleted` as a
  fact about a file that was still there. Measured: `chmod 000` on the parent
  directory turned `modified` into `deleted` for the same file.

  It now reports `unknown` / `stat_error` when the stat cannot answer, and keeps
  reporting `deleted` when it genuinely can. This is the second route to that
  wrong answer; CB-79 closed the first one line below, where a git failure was
  swallowed into an empty rename result.

- **A benchmark import no longer invents a date or run id from a falsey wrong
  type** (CB-82). `import_csv` resolved two of its arguments with truthiness —
  `date or utc_now()[:10]` and `run_id or _next_run_id(conn)` — so `date=[]`,
  `date={}` and `date=""` were all indistinguishable from *not supplied*, and
  the row landed under a date and id the caller never chose. Measured: `date=[]`
  stored today's date and reported success.

  All five non-payload arguments are now validated before anything is parsed or
  written, so a refusal costs no partial work, and every failure is the
  `ValueError` the module contract promises instead of a raw
  `sqlite3.ProgrammingError` or `TypeError` from `json.dumps`.
  **On a write path `None` is the only "not supplied" signal** — deliberately
  unlike a query filter, where `""` legitimately means "no filter": an absent
  filter matches everything, while an absent stored value has to be invented.
  `import_json` forwards these arguments here, so both entry points are covered.

  `tags`/`meta` are serialized **once** and that exact string is stored, so a
  container cannot present one view to the check and another to the write.
  Non-string tag members and non-string `meta` keys are refused, because
  `json.dumps` does not complain about either — it writes `[1, 2]` and silently
  coerces a non-string key — and a non-string tag later crashes `bench-list`,
  which does `",".join(tags)`. The NaN/Infinity policy is **unchanged**.

  Behaviour change: `date=""` and `run_id=""` are now refused where they used to
  default silently. Nothing in the repo passes them.
- **`OSError` no longer escapes from sources that are not file opens** (CB-79).
  Two verified holes. `codebugs reqs-verify` printed a raw `FileNotFoundError`
  traceback when run from a directory that had been deleted — not hypothetical,
  since a long-lived MCP server outlives the git worktree it started in. And a
  git binary that exists but is **not executable** raised `PermissionError`
  straight out of `provenance.file_status`, because its guard caught only
  `FileNotFoundError` — which covers *git is missing* and nothing else.

  All five `subprocess` guards (four in `provenance.py`, one in
  `db.git_rev_parse`) now catch `OSError`. That is a strict widening —
  `FileNotFoundError` is an `OSError`, so nothing that was handled stops being
  handled — and `subprocess.SubprocessError` stays in each tuple because it is
  *not* an `OSError` subclass, so dropping it would let `CalledProcessError` and
  `TimeoutExpired` escape.

  **A latent wrong answer fell out of doing this and was fixed too:** the rename
  lookup in `file_status` used to swallow its own failure into an empty result,
  and the code below then reported the file as **`deleted`** — stating as fact
  something it had failed to check. It now reports `unknown`.

  Behaviour to know about: `verify_requirements` resolves the working directory
  **lazily**, only for the `tests` check that actually uses it, so `checks=["ids"]`
  and `checks=["status"]` work from a deleted cwd; the `tests` check raises a
  clear error instead. `provenance` degrades to its existing `unknown` vocabulary
  rather than raising, because that is already what it does when git cannot be
  consulted.
- **An export that fails no longer destroys the export it was replacing**
  (CB-76). `codebugs export-csv` and `codebugs reqs-export` wrote through a bare
  `open(path, "w")` with no error arm, so an unwritable path printed a raw
  traceback — and, the half that matters, `open(w)` truncates the destination
  *before* the first byte, so any write failure left the previous good export at
  zero bytes. Measured with a simulated `ENOSPC`: 34 bytes → 0.

  The obvious guard is a trap: `except OSError` alone converts that traceback
  into one tidy line **over a file that is now empty**, and `import_markdown`
  silently skips unmatched lines, so a truncated export round-trips as a
  successful, empty import. The new `codebugs.fsio.atomic_write` writes a temp
  beside the destination and `os.replace`s it only after the handle **closed**
  successfully — quota and `ENOSPC` failures usually surface at flush/close, so
  replacing earlier would install a bad file while reporting failure.

  Differences from `open(w)` are handled rather than left to be discovered: a
  read-only destination inside a writable directory is still refused (`open`
  authorizes on the file, `os.replace` on the directory); FIFOs, character
  devices, file-descriptor aliases and any inode this process already holds open
  are written **in place**, which is what keeps both `export-csv /dev/stdout >
  out.csv` **and** `export-csv /dev/stdout | cat` working — those two resolve
  differently (a regular file vs. a non-existent `/proc/<pid>/fd/pipe:[N]`), so
  the check has two halves; only `FileNotFoundError` counts as "missing", so a
  symlink cycle's `ELOOP` refuses instead of replacing the link; and errors
  report the path you typed rather than a temp name.

  **Three behaviour changes**, all deliberate. A writable file inside a
  **non-writable directory** used to export and now fails cleanly, because
  atomic replacement is impossible there and an errno-keyed fallback cannot tell
  that case from `ENOSPC`/`EDQUOT`. **Block devices** are refused, since a
  partial direct write corrupts persistent bytes. A **socket** destination
  changes only its error code — `open(sock, "w")` already failed with `ENXIO`.

  **What an atomic replace cannot carry**, so you should know before pointing an
  export at a file that matters: the new file is a new inode, so ownership,
  ACLs, xattrs and hard-link aliases of the previous file are **not** preserved,
  and any other hard link keeps pointing at the old content. Nothing is
  `fsync`ed, so a power cut may leave either version — but never a truncated one.

- **`import_csv` refuses a non-text payload instead of leaking a `TypeError`**
  (CB-75). `csv_data` went straight to `io.StringIO`, so `csv_data=5` raised
  `TypeError: initial_value must be str or None, not int` rather than the
  `ValueError` the module contract promises. The CSV twin of CB-72.

  Scope is the **wrong-type** door only. An ordinary `str` payload can still
  raise `sqlite3.IntegrityError` from inside the insert loop (duplicate row
  labels, duplicate headers, a `nan` metric); that is a different defect, filed
  separately, and an earlier draft of this entry wrongly called this "the last
  door in that family".

  The guard reads `issubclass(type(csv_data), str)` and **not** `isinstance`,
  which is spoofable: CPython honours a `__class__` property, so an object
  declaring `__class__ -> str` — `unittest.mock.MagicMock(spec=str)` is one —
  satisfies `isinstance` and then hits `io.StringIO`'s `TypeError` anyway, i.e.
  the leak surviving its own fix. The rule, which is CB-74's lesson in a second
  form: *the guard's predicate must be identical to the consumer's requirement*.

  Two deliberate asymmetries with `import_json`, both stated because they look
  like inconsistencies otherwise. **Bytes are refused, not decoded**:
  `import_json` widened its annotation to accept them because `json.loads`
  already did, but `io.StringIO` never accepted bytes, so nothing can be
  importing that way today and decoding them would be a new feature rather than
  a fix. **No snapshot is taken**: `import_json` must materialize its list
  because `__iter__` and `__getitem__` can disagree (CB-74), whereas a `str`
  cannot present two views, so there is nothing for a subclass to
  desynchronize — and `isinstance` keeps `str` subclasses working.

  `None` was already raising a `ValueError`, but the wrong one — `io.StringIO(None)`
  yields an empty stream, so the failure surfaced as "CSV must have at least 2
  columns", which describes a malformed header rather than a missing payload. An
  empty string still reaches the parser and raises that message, because an empty
  data payload is supplied content (CB-67).
- **A CLI command that reads a file now reports an unreadable path as one line,
  not a traceback** (CB-71). `bench-import`, `reqs-import` and `import-csv` all
  performed a file read that no exception arm covered, so `codebugs bench-import
  missing.csv -b Q` printed a raw `FileNotFoundError` traceback. `bench-import`
  *had* a `try`, whose only arm was `except (ValueError, json.JSONDecodeError)`
  — the arm existed and did not cover the failure the handler performs;
  `_cmd_reqs_import` had no arm at all and additionally leaked its connection.

  The guard covers **exactly the read**. A handler-wide `except OSError` was
  rejected after measuring it: the success `print` runs after the import has
  committed, and on a closed pipe it raises `BrokenPipeError` — an `OSError` —
  so a wider arm would report a landed import as bad input, the CB-15/CB-16
  success-shaped lie. For the same reason `_cmd_import_csv`'s `open` is hoisted
  out of its `with` statement, since that statement owned the whole import loop
  and a naive wrap would have enclosed both committed rows and the loop's own
  stderr diagnostics.

  While the read guard was going in, the pre-existing arm turned out to be
  laundering a post-commit failure already: it spanned the success `print`, so a
  `ValueError` from a closed stdout came back as one tidy line at exit 1 for a
  run that had committed (measured; the run is visible in `bench-list`
  afterwards). The `print` now sits outside the arm, so such a failure surfaces
  as a crash. Making it POSIX-clean instead — SIGPIPE semantics or the
  `dup2(devnull)` shutdown dance, applied once at the `cli.main` boundary — is a
  product decision tracked as CB-78.

  Four reproduced siblings are filed rather than folded in, because each needs a
  different transformation: the two export handlers truncate their target before
  failing, so the honest fix is write-to-temp-then-rename (CB-76); a read
  failure part-way through CSV import has committed rows behind it and needs a
  reporting contract (CB-77); and `os.getcwd()` in `reqs-verify`/`provenance`
  raises from a deleted cwd, which the file-open sweep structurally could not
  see (CB-79).
- **`import_json` now refuses a malformed payload instead of leaking a stdlib
  exception** (CB-72, CB-74). The function checked that its argument was a
  non-empty list, and nothing else, so two inputs walked past the module's
  contract — *domain functions raise `ValueError` for invalid input* — and out
  to the caller unchanged: a payload outside `str | bytes | bytearray | list`
  as `TypeError` from `json.loads`, and an array whose **elements** are not
  objects as `AttributeError` from `data[0].keys()`.

  The second is the one that mattered: the MCP wire type is `str | list | None`,
  so `codebench_import(benchmark="b", json_data=[1,2])` reached it from a
  client, and the SDK pre-parses a wire string `"[1,2]"` into a list as well.
  The first is in-process only — pydantic refuses a dict before the wrapper
  body runs.

  The guard is a **positive shape check placed before `data[0]`**, never a
  rewrap of `TypeError`/`AttributeError`: a blanket rewrap would also convert a
  post-commit failure inside `import_csv` into a `ValueError`, which
  `_cmd_bench_import`'s arm then reports as bad input for a write that already
  landed — the CB-15/CB-16 lie, re-entering through its own fix. **Every**
  element is checked rather than `data[0]`, because `[{"a":1,"b":2}, 5]` clears
  a first-element check and dies later inside `csv.DictWriter` with the same
  `AttributeError`.

  A supplied list is also **materialized once** and only that snapshot is
  validated and consumed. The check iterates while the code after it *indexes*
  (`data[0]`) and iterates again, so a `list` subclass whose `__iter__`
  disagrees with `__getitem__` could show mappings to the guard and a
  non-mapping to `data[0]` — CB-74's exact `AttributeError`, surviving inside
  its own fix. Validating one view while consuming another is not a guard.

  Two things deliberately still work, each with a test pinning it: `bytes` and
  `bytearray` payloads (accepted by `json.loads`, importing successfully today —
  refusing them would be a behaviour change wearing a bugfix costume, so the
  annotation widened to `str | bytes | bytearray | list` instead), and mappings
  that are not `dict` (`MappingProxyType`, `OrderedDict`), since the guard tests
  `collections.abc.Mapping`.

  One **deliberate narrowing**, stated rather than glossed: a row object that
  merely duck-types `.keys()`/`.get()` without registering as a `Mapping` does
  import on the old code and is refused now. *"An array of objects"* is the
  documented contract, the refusal is loud and at the boundary, and a test
  records the decision so it can be revisited if a real caller appears.
- **`status="deferred"` now honours every other filter instead of discarding it**
  (CB-28). The MCP `query` / `reqs_query` deferred branch forwarded only `limit`
  and `offset`, so `query(status="deferred", severity="critical")` returned **every**
  deferred finding and `reqs_query(status="deferred", priority="must")` every
  deferred requirement. The arguments were known, correctly spelled and correctly
  typed — they passed every validation the package has — and the call returned a
  success payload, so the caller could not tell. `staleness_check(finding_id=…)`
  had the same shape, discarding `status` / `category` / `file` despite its
  docstring promising to forward them.

  `deferred` is a pseudo-status and now resolves to an id restriction, letting the
  owning domain apply its own filters — the shape the April blockers design already
  specified. `blocker_count` annotation and the CB-20 ranked ordering are unchanged,
  and `group_by` works on the deferred path as a result.

  Three sites **refuse** instead, because no path could honour the argument:
  `release_item(status="abandoned", commit=…)` (an abandoned item is reopened and has
  no commit to record), `set_item_status` when the status already matches (no write
  happens — use `mark_integrated`), and `query_findings(meta_value=…)` without
  `meta_key` (the MCP description already declared the key required).

  `blockers.query_deferred_entities` is superseded for this path and kept only for
  its ordering tests.
- **A falsey non-string vocabulary filter no longer silently disables the filter**
  (CB-25). Every vocabulary filter guarded with plain truthiness — `if severity:` —
  which conflates `None` (not supplied), `""` (the documented empty-filter
  convention) and `0` / `False` / `[]` / `{}` (wrong input). CB-19 put the
  non-string refusal inside `types._resolve`, but a falsey value never reached it:
  the guard short-circuited, the condition was never added to the `WHERE` clause,
  and the caller got the **whole table** back. An unfiltered queue is
  indistinguishable from a correctly filtered one, so
  `query_findings(severity=0)` read as a successful, empty-severity query.

  Fixed at `query_findings` (`status`, `severity`), `query_requirements`
  (`status`, `priority`), `reqs_search_similar` (`status`), and
  `staleness_check` / `check_findings`, whose contract differs — `None` and `""`
  mean "default to `open`" there, not "no filter".

  The same sweep closed three filters that validated their vocabulary on the write
  side only: `codemerge_sessions` (`types.MERGE_STATUSES` already existed but was
  dead code, leaving the CHECK constraint as the only enforcement — it is now
  actually used), `milestone_list` (`kind` / `state`, which had `MILESTONE_KINDS` /
  `MILESTONE_STATES` all along and never consulted them on query), and
  `blockers_query` (`trigger_type`, whose validation sat *inside* the truthy guard
  and was therefore skipped wholesale by a falsey value). All three now raise
  `ValueError` on an unknown value instead of returning everything for a falsey one
  and nothing for a misspelled one.

  **Unchanged on purpose:** `None` and `""` still mean "no filter"; list-valued
  filters (`ids`, `tags`) still treat an empty list as "no filter"; and free-text
  filters (`category`, `file`, `source`, `tag`, …) are untouched — they have no
  vocabulary to resolve against, and are tracked as CB-29.

### Changed
- **`severity` now accepts any case, everywhere it is read or written** (CB-19).
  It was the only vocabulary in `types.py` without a resolver, so
  `add_finding(severity="High")` raised while the sibling `resolve_priority("Must")`
  returned `"must"`. Two sibling entities answered the same question differently,
  which is an avoidable failure mode for the LLM callers this tracker serves. (The
  `update` tool docstring did say "Exact lowercase only"; the `add` tool's did not,
  so the strictness was discoverable on one surface and not the other.)
  `types.resolve_severity()` now normalizes case and surrounding whitespace at all
  five sites: `add_finding`, `batch_add_findings`, `update_finding`, the CSV import,
  and `query_findings`. **Severity still has no aliases** — `crit`, `P0` and `sev1`
  are refused. Only spelling is forgiven, never meaning.

- **Vocabulary query filters now resolve instead of comparing raw text**
  (CB-19 and its sibling sweep). Affects `query_findings` (`severity`),
  `query_requirements` (`status`, `priority`) and `reqs_search_similar` (`status`).
  These compared the caller's spelling against a canonical column while the
  corresponding write paths had always normalized, so a value could be written and
  then not found by the same spelling: `update_requirement(priority="SHOULD")`
  stored `should`, and `query_requirements(priority="SHOULD")` returned **zero
  rows**. The failure was silent — "no requirements" is indistinguishable from an
  empty queue.

  **Behaviour change:** a filter value that is not in the vocabulary now raises
  `ValueError` instead of returning an empty result. The `codebugs query` and
  `codebugs reqs-query` CLI handlers report it on stderr and exit 1 rather than
  printing a traceback, which they did not do before for `--status` either. An
  empty-string filter is still treated as "no filter" and is not validated.

### Fixed
- **A named or declared tracker root must now contain a real database** (CB-23).
  `--repo`, `--tracker-root` and `$CODEBUGS_ROOT` accepted any path holding a
  `.codebugs/` *directory*, and `sqlite3.connect` then created a `findings.db`
  inside it — so a mistyped path, or an export inherited by an unrelated process,
  silently became a second, empty tracker whose writes all reported success.
  `_db_path`'s own docstring already promised this branch would "fail loudly
  rather than quietly become a second, empty tracker"; only the check was
  missing. The refusal names the channel that pointed there.

  **The upward walk is deliberately unchanged**: an existing `.codebugs/`
  directory is still the opt-in, and a database is still created inside one that
  has none. That is what makes an interrupted `codebugs init` self-heal — the
  directory is created before the database — and standing inside a directory is
  evidence about where you are in a way a named path is not.

  Structurally, the open-and-migrate half of `connect()` is now `_open()`, and
  `init_project` is its only other caller. Before, `init` created its database
  *by way of* `connect`, so tightening discovery broke the one function allowed
  to create. The named and declared routes open through SQLite's `mode=rw` URI,
  so "must already exist" is enforced by the open itself — the path check alone
  would be a check-then-act, and another agent removing the database in that
  window would get a fresh empty one built for it.

- **`codebugs where` and the MCP preflight now say when the tracker they name
  does not exist yet** (CB-23). `describe_root()` gained an `exists` field,
  reported separately from `error` because resolving is not the same as being
  there: on the walk route a `.codebugs/` with no database resolves cleanly and
  the next write creates the tracker, so nothing errors and nothing was visible.
  That is the CB-13 misbinding's exact shape — the root it mis-binds to is a
  stray directory — and `where` used to print it as the project's tracker.
- **Concurrent `meta` updates no longer erase each other** (CB-24).
  `update_finding` and `update_requirement` merged `notes` / `append_note` /
  `meta_update` in Python over a row they had read in a *separate* statement, then
  wrote the result back — so two writers that both read before either wrote both
  returned success and the later silently discarded the earlier's merge.
  `busy_timeout` serializes the writes and does nothing about the read preceding
  them. This is the harm CB-18 was filed to prevent, reached by another route: the
  tracker exists to coordinate parallel agents, and `append_note` — the one
  operation whose entire purpose is to be additive — is the likeliest to be issued
  concurrently. Both bodies now sit in `db.txn`, so the write lock is held from
  before the read. `milestones.triage_dismiss` gains atomicity as a side effect:
  its dismissal, audit row and status write now commit as one unit, where the
  nested call used to commit the dismissal early.
- **An `EntityKind` carrying a malformed SQL identifier is refused at construction**
  (CB-22). `entities.py` claimed, in a comment, that every interpolated SQL
  identifier (`table` / `sort_col` / readable column) was pattern-checked; only
  `sort_col` was, inside `order_by()`. The `readable_cols` allowlist was the
  material gap — its membership check guards the *caller's* argument and never the
  allowlist's own contents, so a kind declaring `(SELECT meta FROM findings)` as a
  readable column passed the check and `EntityRef.field()` returned a column that is
  not in the allowlist at all. `EntityKind.__post_init__` now validates all three,
  and the pattern lives once as `types.is_sql_identifier()` — applied with
  `fullmatch`, since the previous `^…$` form also matched a trailing newline.
  Exposure was prospective: `ENTITY_KINDS` is a frozen tuple of literals, but the
  test suite already constructs kinds dynamically.
- **`pull_next` no longer loses an agent's capacity increment** (CB-22 sibling).
  `milestones/capacity.py` built the column name `f"{size}_held"` and interpolated
  it, guarded only by a CHECK constraint two layers away on another table. The two
  paths disagreed for identical input: with an existing capacity row an unknown size
  raised `OperationalError`, while with no row it wrote a row of zeros and returned
  **success**, silently dropping the increment. Both now raise `ValueError`.
- **Queues are ordered by declared severity/priority precedence instead of
  alphabetically** (CB-20). `severity` and `priority` are TEXT columns, so a bare
  `ORDER BY` sorted them lexically: findings came back `critical, high, low,
  medium` — ranking `low` above `medium` — and blocked requirements came back
  `could, must, should`, putting the *highest* priority last. Under a `LIMIT`
  this was not cosmetic: asking for the top 3 of a queue holding 3 `medium` and
  3 `low` findings returned three `low` ones and truncated every `medium`.
  Affects `query_findings`, `get_summary`'s severity breakdown, and the deferred
  entity query in `blockers`. The rank is now derived from the `SEVERITIES` /
  `PRIORITIES` tuples via `types.rank_case_sql()`, so the ordering cannot drift
  from the vocabulary, and unrecognised values sort last rather than first.

### Added
- Findings can be **re-triaged**: `severity` is now accepted by
  `update_finding()`, by the `update` MCP tool, and as `codebugs update <id>
  --severity <critical|high|medium|low>` (CB-17). Severity was previously
  write-once, so a card whose impact was re-measured after filing could not be
  corrected in the structured field — the correction had to be carried as prose
  in a note, which is exactly the state a tracker exists to prevent. This brings
  findings level with requirements, whose `priority` was already mutable.
  Validation matches what `add_finding` accepts — see the severity normalization
  entry under Changed, which relaxed both together.
- `codebugs resolve-trailers --range <BASE>..<HEAD> [--repo DIR] [--dry-run]`
  (provenance module): parses `Resolves: CB-N` / `Tightens: CB-N` trailers from
  commit bodies in a git range and flips findings in-process — `Resolves` →
  `fixed` (skipped if already terminal), `Tightens` → appends a progress note.
  Project-agnostic: any repo's `worktree-finish.sh` can call it to auto-close
  findings on integration instead of copying a per-project script. Also exposed
  as `provenance.resolve_trailers(conn, ...)`.
- `--mode` flag for both MCP server and CLI: `findings`, `reqs`, or `all` (default)
  - `codebugs-mcp --mode findings` — exposes only the 7 findings tools
  - `codebugs-mcp --mode reqs` — exposes only the 11 requirements tools
  - `codebugs --mode findings summary` — CLI with filtered subcommands
- `in_progress` finding status for agents claiming tasks
- Status aliases: `done`/`resolved`/`implemented`/`closed` → `fixed`,
  `wontfix` → `wont_fix`, `invalid` → `not_a_bug`,
  `active`/`working`/`in-progress` → `in_progress`
- `resolve_status()` helper in `db` module
- Schema migration for existing databases to support new status

### Changed
- MCP server refactored: tools registered via `register_findings_tools()` / `register_reqs_tools()` instead of module-level decorators. `FastMCP` instance created in `main()` instead of at import time.
- `update_finding()` and `query_findings()` now accept aliases in addition to canonical statuses

### Removed
- `codebugs-findings` and `codereqs-mcp` entry points (use `codebugs-mcp --mode findings|reqs` instead)

## [0.1.0] - 2025-05-01

### Added
- Core finding tracker: add, update, query, stats, summary, categories
- Batch add support for bulk imports
- MCP server (`codebugs-mcp`) with full tool coverage
- CLI (`codebugs`) with add, update, query, stats, summary, categories, import-csv, export-csv
- Requirements tracking module with add, update, query, stats, summary, verify, import/export
- Embedding storage and cosine-similarity search for requirements
- SQLite backend with WAL mode and JSON metadata support
- Test suite (94 tests)
- README, LICENSE (MIT), CONTRIBUTING guide
