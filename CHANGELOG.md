# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **The safety note on the embedding tools now says only what is actually checked, and a new check
  makes the narrower statement worth more than the old wide one (CB-190).** A previous release
  promised that "a check refuses any network capability being imported into the package". The check
  was real, but it worked from a list of module names somebody had written down, so a network
  client nobody had listed — `cohere`, `ollama`, `httplib2` were the ones measured — passed it
  without a murmur. The `reqs_embed` description no longer makes the wide promise. What replaces it
  is two narrower statements, both mechanically enforced: codebugs' own source imports none of the
  socket-opening modules that check lists, **and** it imports nothing at all from outside the
  package and the standard library unless that exact import is declared, by name, with a written
  reason. The second half is the one that holds up against a client nobody anticipated, because it
  refuses by default instead of recognising. **What has not changed is the promise that actually
  matters to you:** you compute the embedding vector yourself, the tools never receive your
  requirement's text, and the vector goes into this tracker's own local database and nowhere else.
  That was true before and is untouched — only the claim about the package's *capability* was too
  wide, and it has been brought back to what can be demonstrated.
- **`resolve-trailers` can be re-run over a range it has already processed without annotating the
  same cards twice.** Pointing the verb at a widened revision range is the natural thing to do —
  "everything since the last release", then "since the one before" — and every widening re-covers
  commits it has already handled. Until now each pass appended the *same* note to the card again and
  moved its `updated_at`, so a card accumulated identical lines and its date stopped meaning "when
  something last happened here" and started meaning "when I last swept a range that mentioned it".
  A card that already carries this trailer's note verbatim is now left completely alone — nothing is
  written, so neither the duplicate line nor the date change occurs — and it is reported under a new
  `already_applied` bucket instead of silently, because a run that quietly does nothing is
  indistinguishable from one that did the work. The command prints a line per such card and closes
  with `N finding(s) updated, N skipped, N already applied.`; `--dry-run` predicts the same thing,
  since the check is a read.
  **What decides is the note's own text**, which is possible because every part of it comes from the
  commit rather than from the run. So there is one case where a note still arrives twice, and it is
  worth knowing about: if someone edits the stored line by hand, it is no longer recognisable and
  the next run appends it again — exactly the old behaviour, in that one case and no other. The
  first application of a trailer is unchanged, and a `Resolves:` on a card that is already closed
  keeps reporting `skipped (already terminal)` as before.

- **Naming a code location and then contradicting it in `meta` is now refused instead of quietly
  anchoring somewhere else.** `codebugs add -l 10 --meta '{"line": 3}'` — and the MCP calls
  `add(lines=10, meta={"line": 3})` and `batch_add` behind it — used to succeed, at exit 0, and
  anchor the card at line **3**. The `10` you typed was accepted, stored beside it, and never
  reached the anchor.
  The cause was that the two inputs are spelled differently. The dedicated location input writes
  `meta.lines`, and the guard in front of it compared that against the `lines` key of `meta` and
  nothing else — while the capture grammar that actually decides an anchor prefers the *singular*
  spellings, so a `meta.line` sitting beside it won without either side noticing. The same held for
  `meta.site`. Nothing was dropped and nothing errored: both keys really were stored, so the stored
  card looked correct while the anchor pointed at a place you had not asked for.
  The guard now asks the grammar the question that actually matters — **would the place I named be
  the place that results** — and refuses when the answer is no. The message names both places and
  the key that would have won, so the fix is one edit rather than an investigation. It fires only
  when you supply the dedicated input: a card carrying two `meta` keys and no `-l/--lines` is
  untouched, and the grammar's own priority still decides there.
  **Three things deliberately still pass**, because they are not contradictions: the two spellings
  naming the *same* place; a neighbouring key the grammar reads no location out of at all (a `sites`
  value holding function names is a real and common shape, and it never took the anchor anyway); and
  any `meta` key that is not a location key in the first place.
  **This will break a caller that relied on the old behaviour** — one passing both spellings and
  expecting the `meta` key to win silently. Pass one of the two, or make them agree. Note the
  comparison is exact: `-l "src/f.py:10"` beside `meta.line: 10` refuses, because one names a path
  and the other does not, and no attempt is made to decide whether they mean the same line. Measured
  across both trackers this repository can reach — 3732 findings — there is exactly one card in
  which two location keys name two different places, and refusing it is the right answer; there are
  **no** cards in which two keys name one place in two different ways. If that ever produces a
  refusal you believe is wrong, the message carries both sides so it can be reported as such.

- **A row limit you actually type is now honoured when you also ask for findings or requirements by
  id.** `codebugs query --id CB-150 --limit 0` used to print the card; `--id CB-150,CB-152 --limit 1`
  printed both. The same held for `reqs-query`, and for the MCP tools `query` and `reqs_query`
  behind them. The cause was a rule inside both query functions that raised any limit smaller than
  the number of ids up to that number, so the id list silently outranked the caller. It was never
  only about zero: **every** explicit limit below the id count was overridden, and the answer came
  back shaped like a success for a request that had not been performed.
  The convenience that rule existed for is kept, on the only case that needs it: **omit the limit
  and a lookup by id still returns every id you named**, however many there are. What changed is
  that naming a limit and naming an id list are no longer in competition — the number you typed
  wins. Under the hood "no limit was named" is now a distinct state (`limit=None`) rather than being
  indistinguishable from asking for a hundred, which is what made the old behaviour unfixable in
  place; `limit` on both MCP tools therefore accepts `null` and defaults to it, and the help text on
  both `--limit` flags no longer carries the "unless `--id` is given" exception.
  **This changes results for a caller that relied on the old behaviour** — one passing a small limit
  beside a long id list and expecting the whole list back. Such a caller should drop the limit
  rather than raise it: the derived page size still widens to fit the ids.

- **Asking a sweep listing for archived entries two different ways at once is now an error instead
  of quietly meaning one of them.** `codebugs sweep-list-items <sweep> --all --archived-only` — and
  the MCP call `codesweep_list_items(include_archived=True, archived_only=True)` behind it — used to
  return only the archived entries and report success. The two flags ask for opposite things:
  `--all` means *archived entries in addition to the live ones*, `--archived-only` means *archived
  entries instead of them*. Given both, the code never even looked at `--all`, so the combination
  returned **fewer** entries than `--all` on its own would have given, with nothing anywhere saying
  a flag had been dropped. On a sweep with two live entries and one archived, `--all` gave three
  rows, `--archived-only` gave one, and the two together gave one — which reads like a filter doing
  its job.
  The call now refuses on **both** surfaces, with a message that names both flags, says what each of
  them asks for, and tells you which one to remove for each of the two things you might have meant.
  On the command line that refusal is exit 1; an MCP client has no exit code to receive and gets a
  tool error carrying the same message. The refusal happens before anything is read, so nothing is
  listed and nothing is changed.
  **This will break a caller that relied on the old behaviour, and that is a deliberate trade.**
  The MCP tool description used to state the suppression outright — "`archived_only`: Show only
  archived entries (overrides `include_archived`)" — so a client could reasonably have written the
  pair on purpose. That sentence was an honest record of a defect rather than a contract worth
  keeping a silently-dropped argument for, and it has been removed along with the behaviour it
  described. If you were passing both, pass the one you actually wanted. The command-line help
  never mentioned the suppression at all, and now says the two cannot be combined (CB-217).

- A negative `--limit` is now an error on **every** verb that has one, not just on the three that
  learned it last release. `recent`, `milestone-audit`, `usage`, `anchor-resolve` and
  `anchor-recapture` used to accept `--limit -1` and hand you the whole table at exit 0 — SQLite
  reads a negative limit as *no limit* — so `codebugs query --limit -1` refused while
  `codebugs recent --since … --limit -1` printed everything, two neighbouring verbs over one table
  answering the same argument the opposite way. All of them now refuse with the same one-line
  message and exit 1. Zero still means zero rows, and omitting the flag still takes each verb's own
  default, so nothing that worked before stops working (CB-208).
- Each of those verbs' `--help` and MCP tool description now states the contract it enforces,
  rather than leaving the reader to discover the refusal by hitting it.
- An empty page no longer claims the tracker is empty when you were the one who asked for nothing.
  `codebugs query --limit 0` over a full tracker used to print `(no findings match)` — a statement
  about the corpus, where the true statement was about the request — and now prints
  `(limit was 0, so no rows were requested — N finding(s) match)`. Same for `reqs-query` and
  `sweep-next`. A genuinely empty result is unchanged, to the byte (CB-210).

### Fixed
- **The record of how integrations go no longer counts an interrupted run as a successful one.**
  `tools/worktree-finish.sh` writes one line per integration attempt into
  `.worktrees/landing-attempts.log`, and the figure people read off that file — how many attempts
  one successful integration costs — was flattering us. A run that was stopped, by Ctrl-C or by a
  `kill`, rather than ending on its own, was written down with exit code `0`, and `0` is the code
  that means *this one went through*. Every interrupted run was therefore counted as a success:
  too many successes, too few attempts behind each one. The instrument was at its most wrong
  exactly when people were most careful, because the prescribed way to run the pre-integration
  review is to start an integration and stop it by hand. A stopped run now records the code it
  really ended with — `130` for Ctrl-C, `143` for a `kill` — so the summary command printed inside
  that script counts it as the failed attempt it was.
  **Lines already in the file are left exactly as they were.** It is a record of what happened, and
  rewriting it afterwards would destroy the only evidence that the fault existed at all, so any
  figure computed over the existing lines still carries the old error and has to be read with that
  in mind. Two further limits, both measured rather than assumed: a run ended by `kill -9`, by a
  closed terminal, or by the machine going away is still recorded as a success or not recorded at
  all, because only the two signals that stops have actually been seen to arrive by are handled;
  and in the narrow window after the merge has already gone through, while the run is only tidying
  up, a stop is now recorded as a failure although the work did land — an error in the opposite,
  safer direction from the one being fixed.

## [0.2.2] — 2026-08-27

This release is about the tracker telling you the truth about itself, and about a card keeping
its place in the code.

`codebugs where` and the MCP server's startup line no longer hand you a stranger's tracker in
silence. Before running anything, `codebugs` walks up from where you are standing looking for the
nearest `.codebugs/`; when a directory on the way up could not be looked into at all — its
permissions changed under you, a mount answered with an error, a `.git` in it turned out to be a
broken link — the walk used to read *I could not look* as *there is nothing here* and carry on
upwards, binding you to whatever it found above. It now says so and names what it had to skip.

A card's "last changed" date means last changed again. Where a card has a pin into the code, the
tracker refreshes it as the file moves — bookkeeping it does for itself — but that bookkeeping
was going in through the same door as a human edit, so a housekeeping pass made most of the
tracker look as though somebody had just been through it. Housekeeping now says it is
housekeeping and leaves the date alone, and a status change can no longer be filed as
housekeeping at all.

Filing a finding, you can now name the line it is on: `lines` on the `add` and `batch_add` tools
takes `"1850"`, a range, a full `path:line`, or a list, and the card keeps pointing at that code
after the file is edited around it. The command line has had this all along; what is new is that
it exists on the surface agents actually file through.

And the text this tracker hands to an AI client, together with the README a person reads, stopped
promising more than the code delivers. A card survives an edit of its file only when the report
named a line or a line range — most cards name a file and nothing more — and that condition is
now stated instead of implied, with `anchor_resolve` to tell you which kind of card you are
holding.

Alongside those: category names can be folded into one another with one command, and a target the
tracker has never seen is refused by name instead of quietly minted; a negative `--limit` is an
error instead of silently meaning "no limit"; and marking a sweep item refuses contradictory flags
instead of ignoring one of them.

**If you use the MCP server, reconnect it after upgrading.** The instructions the server hands
every connecting client are read once, at startup, so a server that is already running keeps
serving the previous text until it is restarted.

### Added
- **Filing a finding, you can now name the line it is on, and the card stays pointed at that code
  after the file is edited.** Pass `lines` to the `add` or `batch_add` tool — `"1850"`, a range
  `"1850-1870"`, a full `"src/thing.py:1850"`, or a list of separate lines — and the tracker records
  not just the number but the surrounding source text and the commit it read it at. That is what
  survives: when the file is edited later and everything moves down forty lines, the card still finds
  its place, whereas a path and a line number typed into the description are just words and go stale
  the moment anyone touches the file. A bare number is usually all you need, because the path is
  already on the card in its `file` field. The command line has had this as `-l/--lines` all along;
  what is new is that it exists on the tool that agents actually file through, and that the tool now
  says what the field is for instead of listing it as an example of arbitrary metadata.
  Two honest limits. Cards filed **before** this change do not acquire an anchor from it — nothing
  goes back and re-reads them. And a card that describes no place in the code — a process decision, a
  whole-file concern, a piece of design — should be filed **without** it: an invented anchor is worse
  than none, and the tracker will tell you it has none rather than guess. Where a token names a
  different file from the card's own, the anchor is declined for the same reason, instead of reading
  one file's line numbers against another.
### Fixed
- **"Last changed" means last changed again: routine housekeeping no longer looks like an edit to
  your card.** The tracker keeps each card's code location pinned to the surrounding text, so the
  card still points at the right lines after the file is edited around it. Refreshing that pin is
  bookkeeping the tracker does for itself — but it was going in through the same door as a human
  edit, so every refreshed card came back reading as though somebody had just changed it. A
  housekeeping pass across the whole tracker therefore reset the "last changed" date of most cards
  in it, all to the same moment.

  Housekeeping now says it is housekeeping, and leaves that date alone. Closing, re-triaging,
  re-tagging or annotating a card still moves it, exactly as before — and a status change can no
  longer be filed as housekeeping at all, so nothing can quietly close a card without leaving a
  date behind.

  **What this does not fix, said plainly: this change repairs the cause and touches no existing
  row.** A date already overwritten by such a pass is not recomputed and not guessed — inventing a
  plausible date inside a tool whose job is to record history would be worse than admitting the
  gap. The only way back is a backup taken before the pass, restored by hand; if you have no such
  copy, treat the affected cards' "last changed" as no older than the day the pass ran, rather than
  as the truth about the card's earlier life. (This project's own trackers were repaired that way
  from a copy dated 23 August 2026, which is why most of their dates read correctly again — that
  was a separate act on the data, not something this release does for you, and it did not recover
  everything: measured on 28 August 2026, four of the 244 cards in codebugs' own tracker still
  carried a stamp from inside the six-second window that pass produced, and nine carried a stamp
  from that day at all. For those few, the advice above is the one that applies.) Cards filed since
  are unaffected, and from now on the date means what it says.

### Changed
- **Four places where a surface still promised more than the code delivers, and the reader was
  acting on the promise.** Starting the MCP server with `--mode usage` was described as a server
  with nothing to offer; in fact it does not start at all — that mode exists only on the CLI, and
  the server refuses the value with exit code 2. `init` under `--tracker-root DIR` was described as
  ignoring the flag and creating where you stand; it creates in `DIR`, and it is the `CODEBUGS_ROOT`
  environment variable — the one you may have exported days ago — that is deliberately ignored for
  creation. The server's startup diagnostic was described as one line to stderr; when no tracker is
  reachable it writes two, the second telling you calls will fail until one is. And the sample
  `anchor-resolve` output had quietly lost a field from the middle of the record, so a reader
  matching it against a real run would have found a key the documentation did not show. The
  anchor example now shows every field it lists, marks where it is an excerpt, and explains that
  `"reason": null` means *nothing to explain* rather than *field missing*.
- **The instructions the MCP server hands every connecting client no longer promise that a card
  survives an edit of its file.** That is true when the report named a line or a line range, and
  only then — most reports name a file and nothing more, so for most cards there is no anchor to
  survive anything. The text now states the condition, and says that `anchor_resolve` is how to
  tell which kind of card you are holding.
- **The two lists of `--mode` values are now compared with each other by the test suite.** They
  disagree — `usage` is a CLI mode and not a server one — and nothing checked that the README's
  account of the disagreement was right, which is how the wrong account above survived. A mode that
  appears on one surface and not the other now turns a test red instead of surprising a reader.
  The same suite's reader of README examples was widened from matching particular spellings to
  recognising the thing itself: a code fence is a fence at any indentation, in backticks or tildes,
  at any length; a table is found by its column names rather than one exact header line. It was
  measured before and after against deliberately falsified README claims, and it catches
  substantially more of them than the version it replaces; what it still cannot see is named one
  by one in its own documentation, which ships in the tree beside it.
- **The README now says what this tracker does differently, and no longer promises things the code
  does not do.** It opened on "durable memory for AI assistants" — true, but true of any tracker —
  and never mentioned the one thing that actually separates this one: a finding here has an
  identity. Filing the same thing twice bumps one card instead of creating two; a card that was
  fixed and comes back is recorded as a regression on the same row; something you already dismissed
  is not quietly reopened; a finding that named a line range can still be found after the file is
  edited around it; and a claim stops two agents fixing the same card. The opening section is now
  about that, and every example in it is real output from a real run rather than an illustration.
  The tool now also admits, in the same place, where it declines to answer — an anchor whose card
  never named a code span comes back as *undetermined with a reason*, not as a confident guess.
- **Several things the README stated were simply false, and one of them broke the very first
  command a new user types.** `codebugs add` with a category the tracker has never seen is refused
  unless you pass `--new-category`, which is a deliberate guard against a typo splitting your
  category set in two — and the README neither used the flag in its first example nor mentioned it
  existed, so the opening command failed with an error the surrounding text did not explain. It is
  explained now. The dependency line promised `mcp>=1.0.0`; the package has required `mcp>=2.0.0,<3`
  since the server moved to the 2.0 SDK class, so the version the README named would not have
  started at all. The `codebugs categories` sample output did not match what the command prints.
- **The module and tool listings were missing about a third of the product.** Five modules — the
  code-location anchors, near-duplicate similarity, typed relations between findings, the grouping
  reports and the usage counters — appeared nowhere at all, and two of those are exactly the
  features the opening section is about. Half of the merge module's tools were unlisted, as were
  three findings tools and one requirements tool. There were two separate module tables that had to
  agree and did not; there is one now, it lists every module, and it marks `usage` as the CLI-only
  mode it is. Fixed counts ("nine modules", "66 MCP tools", and a per-module tool count that was
  wrong for four of the ten rows) have been removed rather than corrected, because a number written
  into prose is a number that goes stale — the running server reports its own catalogue.
- **The README also now says which tools you cannot reach from a terminal.** Eleven milestone tools,
  including `pull_next` and `milestone_close`, exist only over MCP. The README previously showed
  them in a "typical loop" without mentioning that typing them into a shell will not work.
- **The workflow the README teaches is now the workflow the server teaches.** The MCP server sends
  every client that connects a short recommended loop; the README described a different one. They
  are one loop now, with the README following the server, so a reader and an agent are not given
  two different sets of instructions for the same tool.

### Added
- **Every run of `tools/worktree-finish.sh` now records how it ended, so "landing has become
  painful" can stop being a feeling and become a number (CB-176).** Landing a branch runs the whole
  test suite, takes the integration lock, and then refuses if `main` moved while the suite was
  running. That refusal is correct — the tree it tested is no longer the tree it would land on — but
  nobody could say how OFTEN it happens, and the suspicion is that it happens a lot, because
  parallel sessions commit plan notes to `main` continuously and a run takes longer than the typical
  gap between those commits. Each finish now appends one line to
  **`.worktrees/landing-attempts.log`** — a UTC timestamp, the slug you passed, and the raw exit
  code — and nothing else changes: no new output, no new command, no delay, and **not one exit code
  is altered on any path**. To turn the file into the two numbers that matter, mean attempts per
  landing and which refusals are costing them:

      awk '{n++; c[$3]++} END {printf "%s attempts per landing (%d attempts, %d landings)\n", (c["0"]?sprintf("%.2f",n/c["0"]):"n/a"), n, c["0"]+0; for (i=1;i<256;i++) if (i in c) printf "  exit %d: %d\n", i, c[i]}' .worktrees/landing-attempts.log

  It prints, for example, `2.00 attempts per landing (4 attempts, 2 landings)` followed by a count
  per refusal code, and `n/a` rather than a made-up `0.00` when nothing has landed yet. The log
  lives beside the integration lock in `.worktrees/`, which is already ignored by git: it is local
  to your clone, no other session or checkout reads it, and you can delete it at any time. The
  recording is deliberately unable to interfere with your work — if the file cannot be written, for
  any reason at all, the finish carries on and ends with exactly the code it would have had
  otherwise. Note that a line with code `15` is a landing whose merge DID happen (the post-merge
  alarm fired), which is why it is counted separately from a clean `0`.
- **The test suite now tells you when the source tree changed while the run was in progress
  (CB-215).** Several tests here read source files straight off the disk, and the suite is often
  re-run in the main checkout — the same place other branches are merged. When a merge, an editor
  save or a formatter lands halfway through a run, the tests that ran before it and the tests that
  ran after it were reading two different trees, and the failures that come out of that look exactly
  like a real regression. People then go and debug code that is perfectly fine. The run now takes a
  fingerprint of every file in the tree before the first test and again at the end, and if anything
  differs it prints a short block in the final summary naming the paths that moved, next to the
  failures it explains. **It never changes the exit status of the run** — a tree that moved is
  ordinary, and turning that into a failure would just be a new kind of false alarm — and it says so
  in the message itself, so the block is a report and never a verdict. **On a tree that did not move
  it prints nothing at all**, not even a heading. The list is not filtered by any judgement about
  which paths matter: a note landing in `.claude/plans/` is reported exactly like a change under
  `src/`, because only you know which test went red and therefore whether the path is relevant. Only
  directories that are not a source of anything are left out — git's own directory, the virtual
  environment, the worktree directories, the tracker's own database, and build caches. The commit
  name is shown beside the paths when git can supply it, and is simply left out when it cannot; a
  tree unpacked without a git directory works exactly the same way, because the decision is made
  from the files and never from the commit.

### Changed
- **You can merge category NAMES into one another with one command, and the help finally
  says so (CB-222).** `categories-normalize` has always been able to do two different jobs,
  and its help, its tool description and its docstring described only the first: folding
  variant SPELLINGS of one word (`Process Improvement` → `process_improvement`) to a single
  canonical form. The second job is the one you reach for when a tracker has accumulated too
  many category names — renaming a rare name into a common one, where the two are not
  spellings of each other at all:

      codebugs categories-normalize --fold-map '{"data_loss": "correctness"}'
      codebugs categories-normalize --fold-map '{"data_loss": "correctness"}' --apply

  That worked before this release; nothing in the three places you would look said it did, so
  anyone who needed it concluded the tool could not do it. Nothing about the operation itself
  changed — the first command above is still a **dry run**, which is the default, and it
  writes nothing at all. What you get back is the list of rows that would move, and a
  `from → to` table. Renamed rows have their derived `auto:v1` fingerprint re-computed with
  the new category, so a later report of the same defect still lands on the same card; a
  fingerprint you supplied yourself is left byte-identical. If the rename would put two LIVE
  findings on one fingerprint the run writes **nothing at all** and names the pair — merging
  two cards is your decision, not a migration step.
  **A key that matches no stored category is accepted and renames nothing — and the report
  now names it** (see the CB-207 entry below), so a typo on the left-hand side no longer has
  to be spotted by reading the `from → to` table against your own map.
  **And take a backup first:** `codebugs export-csv backup.csv` writes the findings, and
  `codebugs restore-csv backup.csv` puts them back verbatim — ids, statuses, occurrence
  counts and fingerprints — into an **empty** tracker. Its limit is real and worth knowing
  before you rely on it: milestone items and the audit history are not part of a CSV export
  and are not restored.
- **The category-fold dry run now answers the question you run it for (CB-207, CB-209).** Its
  whole purpose is to tell you whether the fold will do what you meant, and there were two
  things it did without saying so. **A line of your map that matched nothing is now named.**
  If you write `--fold-map '{"data_looss": "correctness"}'` and no card is stored under
  `data_looss`, that line renames nothing — it never did — but the report used to be silent
  about it: every counter looked healthy and the rename simply did not happen, and the only
  way to notice was to read the `from → to` table against your own map line by line. Such keys
  are now listed, on the command line and under `unmatched_fold_keys` in `--json`. Matching is
  exact against the spelling that is stored, so if a tracker still holds
  `Process Improvement` you have to type it that way; a key written in canonical form is
  reported as matching nothing, which is the truth about that run. **And you are now told
  which cards the fold puts on one identity.** The command has always refused to give two OPEN
  findings the same fingerprint, because merging two live cards is your decision. It said
  nothing about any other pair. When **both cards are closed**, that matters: the next time the
  defect is reported only one of them can be revived, and the other stays behind for good. When
  one is closed and the other still open — which is what closing an old spelling fork usually
  looks like — nothing is lost, and the open card simply goes on collecting the reports. Either
  way the fold performs the merge and you were not told; both are now listed under
  `merged_identities`, with each card's status, so you can tell the two cases apart. On the
  command line that list is capped at twenty groups with a count of the rest, because on a badly
  forked tracker it can be long; `--json` always carries all of it. **Both of these are
  messages, not refusals.** The exit code does not change, nothing new is written or withheld,
  and a run with nothing to say prints nothing extra. In `--json` both keys are always present,
  and an empty list there means "checked, none".
- **A fold target that does not exist now needs `--new-category` (CB-223).** This is the one
  behaviour change here, and it is worth stating plainly. Previously,
  `--fold-map '{"correctness": "corectness"}' --apply` — one letter wrong on the right-hand
  side — moved every matching row into a brand-new category called `corectness`, returned
  success, and printed that name in the `from → to` table looking exactly like an existing
  one. An operation whose entire purpose is to make a tracker hold FEWER category names could
  quietly make it hold one more. Now such a target is refused with one line naming the
  nearest existing categories (or the canonical spelling, when your target is a near miss of
  one), and nothing is written. **If you genuinely are renaming a category to a new name,
  add `--new-category`** — the same flag, with the same meaning, that `add` already uses. The
  refusal stops at the first bad target rather than listing them all, so a map with two typos
  takes two runs to clear. The refusal happens on the dry run too, not only under `--apply`:
  a dry run's job is to tell you what would happen, and "it would refuse" is part of that.
  Folding every stored spelling to its canonical form — the command with no `--fold-map` at
  all — is untouched and never asks for the flag.
- **`categories_normalize`'s `apply` and `new_category` are now strictly boolean over MCP.**
  A client that sent `apply: 1`, `apply: 1.0` or `apply: "true"` used to have it quietly
  accepted as `true`, which turned the dry run — the only thing standing between a report and
  a rewrite of every category in the tracker — into that rewrite, for exactly the values that
  most look like a mistake. Those calls are now refused at the validation boundary, before
  anything runs. Send a real JSON `true` or `false`. The command line is unaffected: `--apply`
  and `--new-category` are flags and were never coercible.
- **Marking sweep items now refuses `--state` together with `--undo`, instead of silently
  doing the opposite of what you asked (CB-197).** `codesweep_mark` takes two ways of saying
  where an item should go: `state="done"` names one state outright, and `processed=True/False`
  is the older shorthand for "the first finished state" / "the first unfinished one". If you
  sent both, the whole `processed` argument was thrown away without a word. The worst case is
  the one that reads like success: `state="done", processed=False` marked the item **done** —
  exactly the opposite of the `processed=False` you passed — and returned a normal result. The
  tool's own description did warn you ("Ignored if `state` is set"), but a documented wrong
  answer is still a wrong answer, so the call is now refused with one line naming both values.
  On the command line the same pair is `sweep-mark … --state done --undo`, which now exits 1.
  **What it costs you:** if your client always fills in `processed` — many do, because a
  generated wrapper substitutes the default for every argument — those calls will start
  failing as soon as they also pass `state`. The argument is now optional, so the fix is to
  leave it out; leaving it out means what it always did (the first finished state). **If your
  wrapper cannot omit an argument, send `null` instead** — that is exactly what "not supplied"
  now is, and it is accepted beside a `state`. `state` on its own, `processed: true`/`false`
  on its own, and passing neither are all unchanged.
  **One more change, small but worth knowing because it is a reversal:** sending
  `processed: null` on its own used to be rejected outright by the tool (the parameter was a
  strict boolean, and `null` was not one). It is now accepted and means "not supplied", i.e.
  the first *finished* state. If you call the Python function directly rather than through the
  tool, `processed=None` used to be treated as false and marked the item *unfinished* — it now
  marks it finished, the opposite. Nothing in this repository or its sibling tools passed that
  value, which is why the change is being made rather than worked around, but a caller that
  did would silently get the other answer.
  **The refusal does not care whether the two values agree.** `state="done"` beside
  `processed=True` is refused as well, even where `done` really is the first finished state.
  That agreement is a coincidence of how one sweep's lifecycle happens to be ordered, and a
  rule that quietly permitted the pair on some sweeps and refused it on others would be
  harder to rely on than one that always says no.
- **A negative `--limit` is now an error instead of silently meaning "no limit" (CB-196).**
  On `query`, `reqs-query` and `sweep-next` — and on their tool equivalents `query`,
  `reqs_query` and `codesweep_next` — asking for `--limit -1` used to print the ENTIRE
  table and exit 0. SQLite reads a negative limit as no limit at all, so the one argument
  you use to bound a result was quietly doing the opposite of what you asked, with nothing
  anywhere to tell you. It now refuses with a one-line message and exit 1.
  **What it costs you:** if you were using a negative number as a way to say "give me
  everything", drop the flag instead — omitting it is what "no limit" means on `sweep-next`,
  and on `query`/`reqs-query` it gives you the default page of 100. **`--limit 0` is
  unchanged and still legal**: it means zero rows — except when you also pass
  `--id`/`--ids`, where the id list you named sets a floor and a smaller limit is
  raised to fit it. That was always true; it is now written on the flag.
  The refusal also covers `--status deferred`, which answers from a different code
  path and used to accept a negative limit at exit 0 whenever no deferred rows
  existed — so the same flag no longer gets two different verdicts depending on
  what the tracker happens to contain.
  This does NOT close the class. `recent --limit -1` still returns everything at exit 0,
  along with several internal paths, and nothing mechanical catches a new one — that is
  tracked as CB-208.
- **The "a read never waits on a write" improvement now states its one exception, because it
  has one (CB-202).** Since 0.2.1, a call that only reads the tracker no longer stalls behind
  another session's write. That holds from the *second* time a tracker is opened onward. The
  very first open of a brand-new tracker still has to create the few rows the tracker needs to
  exist, and creating them is a write — so if another session happens to be writing at that
  exact moment, that first open waits for it, just as everything did before. Measured: with
  another session holding the database for 0.7 seconds, the first open takes 0.73 seconds and
  every open after it takes under a thousandth of a second. In practice this is one moment per
  tracker, right after `codebugs init`, and there is no way to avoid it — you cannot skip a
  write by checking first when the thing you are checking for is not there yet. It is written
  down here because "a read never waits" read as unconditional, and it is not.
- **Running the test suite under a stray `.codebugs/` directory now stops immediately with one
  explanation instead of roughly a thousand unrelated-looking failures (CB-204).** This affects
  anyone who runs `pytest` on this project, and nothing about the tracker's own behaviour.
  `codebugs` finds its database by walking up from where it is called — intended behaviour, and
  not changed here — but that means a `.codebugs/` directory sitting anywhere above pytest's
  temporary directory captures every test that meant to build a throwaway tracker of its own.
  An empty `/tmp/.codebugs` left behind by some other tool did exactly that twice in one day, and
  both times the run looked like a large, sudden breakage of the code. Measured: 1071 of 2739
  tests fail or error in that state. The suite now asks the product's own directory walk one
  question before any test runs, and if the answer is "yes, there is a tracker above you", it
  refuses in under a second, names the directory it found, and tells you the two ways out —
  delete the stray directory, or point pytest's temporary root somewhere else with
  `--basetemp` or `TMPDIR`. It stays silent on a healthy machine, including the case where a
  tracker sits above a `.git` directory and is therefore genuinely out of reach.

### Fixed
- **Mistyping a worktree name at `tools/worktree-finish.sh` now reports the code it is documented
  to report (CB-231).** The harness publishes an exit code per kind of refusal so a script calling
  it can tell them apart, and `2` means "there is no worktree by that name". It printed the right
  message and then exited **`1`** — the code that means bad input in general — so the refusal was
  real but you could not tell it apart from any other. The cause was the listing of available
  worktrees printed just below the message: it filters out your main checkout, so in a clone with no
  OTHER worktree it selects nothing, which counts as a failure and killed the script two lines
  before it could report `2`. In other words, the documented code was unreachable in exactly the
  situation it describes — a mistyped name with nothing else checked out. Found while adding the
  landing-attempt journal above, which recorded a `1` where the table promised a `2`.
- **The test-suite alarm that warns you when the source tree moved mid-run could stay completely
  silent over the exact case it exists to catch (CB-226).** A directory that lost its listing
  permission — or that failed to list for any other reason — simply vanished from both the
  "before" and "after" snapshot the alarm compares, so a file changed inside it, in the middle of
  a run, produced no report at all: the run looked perfectly still. The same alarm's own
  truncation line could also make a whole directory disappear on an ordinary tree: with more
  changed paths than its twenty-line display limit, the alphabetical sort put every note under
  `.claude/plans/` ahead of a single changed file under `src/` (the dot sorts before letters), so
  the bare "... and N more" at the end swallowed that one file with no trace anywhere in the
  report — the one path a reader would actually have gone to check. Both are fixed. A directory
  the run could not list is now named in the report as a place it never examined, so the alarm no
  longer calls a run "still" when it simply did not look; and whenever the truncation line fires,
  it now also names every top-level directory that has a change in it, with a count, so a whole
  directory can no longer vanish from the report — only individual files inside an already-named
  directory stay capped, exactly as before.
- **One of the two ways out this alarm's own setup refusal offers you does not always work as
  advertised (CB-225).** When your tracker sits above pytest's temporary root, the refusal
  suggests `pytest tests/ --basetemp="$(mktemp -d)"` as a safe alternative to moving `TMPDIR`. It
  is safe from the deletion risk it was written to avoid, but it does not always escape the
  tracker: plain `mktemp -d` resolves against the very `$TMPDIR` (or `/tmp`) that is already
  contaminated, so copying that line verbatim can print the identical refusal again, at the same
  exit code. The message now says so, and tells you to use the `TMPDIR=` form instead when that
  happens.
- **When the test suite refuses to run, the way out it hands you no longer deletes a directory
  without warning you (CB-214).** The suite stops before any test executes when a `.codebugs/`
  tracker sits above pytest's temporary root, because in that state a thousand tests would fail
  for a reason that has nothing to do with the code. That refusal offered two ways out, and one
  of them read `pytest tests/ --basetemp=/some/other/place`. **pytest empties the directory you
  name there, recursively, before the run starts, and says nothing about it** — so "some other
  place" pointed at a directory you already had is a directory you no longer have. The message
  said nothing about that, and the harmless neighbouring suggestion (`TMPDIR`, which only adds
  its own subtree beside whatever is already there) sat below it as if the two were equals.
  Three things changed, all in that message. The exits now run from safe to destructive, so the
  first line that fits is the one that destroys nothing. `--basetemp` is still offered — it is a
  legitimate flag and there are cases `TMPDIR` does not cover — but the deletion is now stated
  where it is offered, together with a whole command you can copy as it stands
  (`pytest tests/ --basetemp="$(mktemp -d)"`), because advice you have to adapt is advice that
  gets pointed at an existing directory. And the check the message suggests for deciding whether
  a stray tracker is safe to delete now tells you what its own answer means: it exits 1 saying
  `holds no findings.db`, which is the confirmation that the directory is litter and not a sign
  you typed the command wrong, while an answer that prints statistics means the tracker is real
  and must be left alone. Nothing about when the suite refuses has changed — only what it tells
  you to do next.
- **`codebugs` could bind to somebody else's tracker without a word, and now it tells you when
  it might have (CB-218).** Before running anything, `codebugs` walks up from the directory
  you are standing in, looking for the nearest `.codebugs/`. If a directory on the way up could
  not be looked into — its permissions were changed after you entered it, it sits on a mount
  that answered with an error, a `.git` in it turned out to be a broken symbolic link — the walk
  read "I could not look" as "there is nothing here" and simply carried on upwards. Measured:
  with your project's own tracker sitting inside such a directory and an unrelated `.codebugs/`
  one level above it, `codebugs where` printed a completely clean binding to the stranger's
  tracker and exited 0, with no warning of any kind, and `stats` then answered — truthfully and
  uselessly — about the stranger's empty database while your findings sat one level below,
  unread. The walk still behaves exactly as it always has, including where it stops at a
  repository boundary; what it no longer does is stay silent. `codebugs where` now lists every
  place on the way up it could not examine, with the reason for each, right under the binding it
  reports, and says in words that a tracker may be hidden behind one of them. The MCP server
  says the same thing on start-up, on its error stream, where it matters more — nobody watches a
  server start, and every tool call afterwards would have been reading and writing the wrong
  tracker. And when the walk finds nothing at all, the refusal no longer claims that no tracker
  exists in any parent directory; it says which questions it could not answer, and that this is
  not proof. Nothing here refuses a command that used to work: if a directory cannot be examined
  the walk carries on past it exactly as before, because one unreadable directory belonging to
  somebody else, somewhere between you and your project, must not stop `codebugs` from working.
- **A fourth spot where "I could not look" still read as "there is nothing here" — the one
  place CB-218's own fix left standing, in the corner where git worktrees are recognized
  (CB-224).** When `codebugs` walks up looking for your tracker and crosses into a git worktree,
  it has to tell a worktree apart from a submodule by checking a small marker file inside the
  worktree's own git bookkeeping directory. If that directory could not be examined — for
  instance because something removed its permissions after the worktree was created — the check
  silently answered "not a worktree" instead of "I could not tell", and two things followed from
  that single silent guess. `codebugs where`, run from inside such a worktree, reported "no
  tracker found anywhere" even when your project's real tracker was sitting right there in the
  main checkout, one hop away, with no warning that anything had gone unchecked. And `codebugs
  init`, run in the same spot, no longer recognized that it was inside a worktree at all, and
  went ahead and created a brand-new tracker inside it — a tracker that git deletes the moment
  the worktree is removed, findings and all. `where` now lists this skipped check alongside the
  others it already reports, with the actual reason it failed (for example, "Permission
  denied"), so you know a tracker may be hiding behind it instead of being told none exists.
  Two related checks inside `codebugs init` — whether the directory you are initializing
  actually exists, and whether a `.codebugs/` already sitting there is a directory rather than
  some other kind of file — got the identical honest treatment: an answer that could not be
  established now refuses with the actual reason instead of silently being read as "no, it
  isn't". **Two things this entry claimed, or let you assume, that were not true — both corrected
  by CB-227 below.** The `codebugs init` half of the story above was described and then not fixed:
  the change reached the message `where` prints and never the check `init` makes, so `init` went
  on creating the doomed tracker. That was not a policy question left open, as this entry
  suggested — `init` has refused to create a tracker inside a worktree for a long time, and the
  refusal simply could not fire when the check could not see. And the test that landed here was
  promised as holding "every such check in this file", which was wider than what it did: it
  matched calls by the exact text you happened to write, so the same check spelled another way
  walked straight past it.
- **Two more places where `codebugs` said "there is nothing here" about something it had not
  managed to read — both inside git worktrees, and one of them still cost you a tracker
  (CB-227).** A linked worktree keeps a small `.git` FILE at its root and a matching bookkeeping
  file called `commondir` beside the main repository, and `codebugs` reads both to work out where
  your real tracker lives. Neither read said anything when it failed. If the `.git` file itself
  could not be read — permissions changed after the worktree was created, for instance —
  `codebugs where` reported "no tracker found in this directory or any parent" at exit 1, with
  its list of unchecked places EMPTY, while your project's tracker sat in the main checkout one
  hop away; and `codebugs init`, run in the same spot, created a fresh tracker inside the
  worktree, silently, reporting success — a tracker git deletes along with the worktree, findings
  and all. If instead the `commondir` file could not be read, `where` announced as a plain fact
  that "its main checkout has no tracker either", over a main checkout that had one and that
  `codebugs` had never even located, sending you off to run `init` somewhere you did not need to.
  Now: `where` names the file it could not read and the reason, and says in so many words that
  this is not proof no tracker is there; it no longer claims anything at all about a main
  checkout it could not find. And `init` refuses instead of creating the doomed tracker, telling
  you what it could not read — `--force` still creates one there if that is genuinely what you
  want. **This also closes the `init` half of CB-224 above**, where the same silent read let the
  same doomed tracker be created; only the `where` half had been fixed.
- **The test meant to stop that from recurring was checking the spelling, not the behaviour
  (CB-227).** This one is invisible from the outside, but it is the reason the two holes above
  survived a change written specifically to close holes like them. That test compared each check
  in the file against a list of literal call texts, so writing the identical check a slightly
  different way — importing the function by name instead of through its module, renaming the
  module on import, catching the error under one of its older aliases — made it invisible. It
  also let a note saying "this one particular harmless check is fine" quietly cover a second,
  decisive check added to the same function later. Ten ways of writing the same check a little
  differently were tried against the old test and the new one: the old caught none of them, the
  new catches all ten, and both still catch the two plain cases that must always be caught. It
  now works out what a call actually DOES rather than how it is written, and a note covers one
  specific check rather than a whole function. A wider sweep of forty evasions is caught fifteen
  times; the twenty-five that get through are now listed by name in the test's own documentation,
  instead of it promising to catch everything.
- **`codebugs where` promised that the next command would create a database, without checking
  whether it could (CB-219).** When a `.codebugs/` directory exists but holds no database yet,
  `where` says "no database there yet — the next command creates one". That is usually right —
  it is how a half-finished `codebugs init` heals itself. But it was printed without ever asking
  whether the directory could be written to, so with a read-only `.codebugs/` you were told the
  next command would create a database, at exit 0, and the very next command refused with
  "cannot open findings.db … for writing". The fact is still reported; only the promise is now
  conditional. Where the directory looks unwritable you are told so instead, and the MCP server's
  start-up line changes the same way. As with the other writability warnings here, only the
  negative answer is printed — a permission can be withdrawn between the check and the write, so
  saying "this will work" would be the same kind of false confidence, merely inverted.
- **The safety net protecting that improvement was checking less than it promised (CB-202).**
  A test in this repository exists to catch the one-line mistake that would silently bring the
  old stalling behaviour back — the symptom is invisible unless two sessions happen to collide,
  so nobody would notice until it hurt. An independent reviewer restored the defect completely
  and the test stayed green, because it was looking for the mistake written one particular way
  rather than for the mistake itself. It now checks what actually matters: whether the tracker's
  start-up code writes anything without first looking to see whether the write is needed — no
  matter how that write is spelled, including when it is hidden in a block of setup instructions
  or in a helper called from somewhere else. The test also states, in its own text, the cases it
  still cannot see, so the next person to rely on it knows exactly how far it reaches. Nothing
  about how codebugs behaves changes here; what changes is that the promise above is now
  actually guarded.

- **`codebugs where` no longer tells you a tracker is missing when it simply could not
  look at one.** If the permissions on a `.codebugs/` directory stopped the tool from
  entering it — for instance because the directory lost its execute bit — `where` printed
  "no database there yet — the next command creates one" and exited successfully, while
  every other command refused to open the tracker that was sitting right there. That
  sentence is a promise about the future, and it was the opposite of the truth: nothing
  was going to be created, because the database already existed and was simply out of
  reach. The same false line appeared when something other than a database file occupied
  that name — a directory, a named pipe, a socket, a symbolic link that leads nowhere —
  and it appeared in the startup message the MCP server writes to its log as well.

  `where` now distinguishes three answers instead of two: a database is there, it is
  proven that nothing is there, or it could not be established either way. Only the
  second still promises that the next command will create a tracker. The third prints
  what stopped the tool from looking, so you can act on it — for example `(could not
  confirm a database there — could not look at it (Permission denied))`. Naming a tracker
  explicitly with `--tracker-root` or `--repo` is covered too: those refuse as before,
  but they no longer say a tracker is absent when the real reason is a permission wall, a
  path longer than the system limit, or something else standing in the database's place.

  One boundary is worth knowing, because it is unchanged: this reports on the *path*, not
  on the *contents*. A `findings.db` that has been corrupted still looks perfectly healthy
  to `where`, and the failure only appears when a command opens it.

## [0.2.1] — 2026-08-26

This release is about two things: working alongside other sessions without getting in each
other's way, and the tool telling you the truth about what it did. Before this, any call to the
tracker — even one that only reads — could stall for several seconds, or fail outright, if
another session happened to be writing at the same moment; a read call no longer waits on a
write at all, and a tracker whose database file is read-only can finally be read instead of
being refused outright. The second half closes three cases where the tool gave you no answer or
the wrong one: exporting to your own standard output or standard error no longer corrupts the
file you asked for; the warning that a tracker's database may not be writable no longer
disappears when you redirect the error stream; and asking the program which version you are
running now gets an answer from the program itself, not from the package manager.

### Added
- **`codebugs --version` prints the version you are running (CB-191).** Asking which
  codebugs you had used to mean asking the package manager (`pip show`, `pipx list`)
  rather than the program. It works in any directory, with or without a tracker, and in
  every `--mode`.
- The startup message a connecting agent receives now guards its own eight recommended tool
  names against the live tool catalogue, not just against a wording check (CB-189). Renaming or
  removing one of those tools will fail the test suite instead of silently leaving the agent with
  a working loop that recommends a call which no longer exists.

### Changed
- **A limit of `0` now means "no records", and a limit that cannot be honoured is
  refused instead of silently ignored (CB-161, CB-162).** Three places took a limit and
  two of them did the opposite of what you asked. On `codebench_query` and
  `codebench_list` (and their `bench-query` / `bench-list` verbs), `last_n=0` returned
  **every** run rather than none, and a negative limit did the same, because SQLite
  reads a negative `LIMIT` as no limit at all. Both now mean what they say: `0` gives
  you nothing back, and a negative number is an error rather than a silent
  "everything". `codesweep_list_items`' `limit` already gave zero entries on a zero and
  still does — only its negative case changed.

  **An argument that the chosen path cannot honour now fails rather than disappearing.**
  `codebench_list` without a benchmark lists benchmark *names*, which have no runs to
  limit, so a `last_n` passed there was accepted and quietly dropped while the call
  reported success; it is now an error that tells you to name a benchmark or drop the
  argument. Likewise `codesweep_archive_items` with an explicitly empty list of items
  archives nothing, so a `where_status` or `older_than` sent along with it was never
  read — that combination is now refused. An empty item list on its own is still
  perfectly legal and still answers `archived: 0`.

  **One narrowing, and exactly where it can reach you.** A limit written as a string
  (`"5"`), as a fraction (`2.7`), or as `true`/`false` used to be accepted by these
  functions — interpolated as text in one place, rounded down by a cast in another — and
  is now an error. **If you call codebugs as a Python library, that is a change you can
  trip over; through the MCP tools or the CLI it is not**, because both surfaces already
  declare these arguments as integers and convert or reject before the tracker sees them
  (measured: over MCP a JSON `"5"` arrives as `5` and `false` as `0`, while `2.7` was
  refused there before this change too; on the command line `--last-n`/`--limit` are
  `type=int`). What DOES reach every surface is the pair above: a `0` now means none, and
  a negative number is refused.
- **Telling the tracker that two cards are different defects now stops the citation
  report merging them (CB-62).** `grouping_citations` groups cards by the `CB-…`
  references people write in card text, on the assumption that a reference means the
  two cards are one piece of work. That assumption is sometimes wrong — a card can
  mention another to contrast with it, or to point at a trap — and until now there was
  no way to say so: `relations_relate(a, "distinct_from", b)` recorded your judgement
  and the report ignored it. It no longer does — that reference stops joining the two
  cards, whichever order you declared the pair in, and retracting the declaration
  (`relations_unrelate`) brings the grouping straight back.

  **The reference itself is still reported.** Somebody did write it, and hiding it
  would destroy evidence rather than correct a conclusion, so the citation stays
  visible with its quoted context — under a new `suppressed_edges` list, and on the
  `codebugs grouping-citations` header line as `suppressed=N`. That is deliberate: a
  `distinct_from` written by mistake would otherwise be invisible in the one report it
  silences, and you would only see two cards that mysteriously never group.

  **What it does NOT promise, because dropping one reference does not split a group.**
  If a third card cites both of yours, the two are still in one component through that
  third card. The report says so rather than letting you assume otherwise: each entry
  carries `still_grouped`, the header line shows `still_grouped=N`, and the CLI marks
  such a pair `STILL GROUPED`. So the guarantee is "this reference no longer counts",
  not "these two can never appear together" — separating them for real would mean
  deciding where the third card goes, which is a question nobody has asked yet.

  **`grouping_filing` is untouched**, and that is a decision rather than an omission.
  It groups cards by the split lineage you recorded on purpose; overriding one thing
  you declared with another thing you declared is a different question — which of the
  two records wins — and it is not answered here.

### Fixed
- **`codebugs where` no longer loses its writability warning when stderr is redirected
  or discarded (CB-182).** The warning that a tracker's database may not be writable
  (added for CB-100) printed to stderr, while its neighbour on the same three-line
  table — the note that a `.codebugs/` directory has no database in it yet — printed to
  stdout, even though neither one is an error and the exit code stays `0` on both.
  `codebugs where 2>/dev/null`, the ordinary way to get a clean view or feed a script,
  therefore made the writability warning vanish entirely and quietly brought back the
  exact problem CB-100 was meant to fix: an unwritable tracker looking healthy. The
  warning now prints alongside the rest of the table, in stdout, like its neighbour.
  The branch for a genuinely unresolved tracker root is unchanged — that one really is
  an error and stays in stderr, at exit `1`.
- **The relation commands now tell you what went wrong in one line instead of a page of
  traceback (CB-193).** Naming a card that does not exist — `codebugs relations-relate
  CB-1 distinct_from CB-2 --source probe` on a tracker with no `CB-1` — used to end in a
  Python stack trace; it now says `codebugs: No such finding: CB-1` and stops, the way
  most other commands in the package already did. Retracting a relation that is not there
  behaves the same way. `relations-query` was brought into line at the same time, though
  in its case nothing you can type reaches that path today — its `--rel` is checked by the
  argument parser before the command runs. The exit code is `1`, as it already was, so
  scripts that test it are unaffected; what changes is what a person reads.

  **Scope, stated because the rest is real**: this is one module, not the class. Other
  commands still answer a bad argument with a stack trace — `milestone-list --kind
  nonsense`, `merge-sessions --status nonsense`, and `blockers-add` naming a card that
  does not exist all still do. The sweep of the remaining handlers is tracked on CB-200.
- **The paragraph opening the 0.2.0 notes below made four wrong claims about that
  release, and has been corrected (CB-191).** It counted two strict CLI arguments where
  there is one, said neither corpus-wide clean-up had been run when the category fold
  had already been applied here, left out that an un-folded older tracker files a
  duplicate instead of counting a repeat report, and pointed at a "BREAKING" section
  that does not exist. The release itself is unchanged; only its description was wrong.
- **Exporting to your own terminal's redirect no longer corrupts the file (CB-143,
  corrected and completed by CB-194 below).** `codebugs export-csv /dev/stdout >
  out.csv` used to report success and leave a broken CSV behind: the final "Exported N
  findings" confirmation landed on top of the file's own header, silently, with no
  error and no warning. `reqs-export /dev/stdout > out.md` carried the identical
  defect. The confirmation is steered off the file whenever the destination is your own
  standard output; exporting to an ordinary file or path is unchanged. (This entry
  originally said the confirmation "now goes to your terminal (stderr)" — true only
  when stdout, not stderr, is the redirected one; see CB-194 for the case that made
  that unconditional claim wrong.)
- **Exporting to your own STANDARD ERROR — or to both streams merged into one file —
  no longer corrupts the file either (CB-194).** CB-143 fixed the common shape
  (`export-csv /dev/stdout > out.csv`) by moving the confirmation to your terminal's
  standard error, but that repair assumed standard error was always a safe place to
  print: `codebugs export-csv /dev/stderr 2> out.csv` redirects standard error itself,
  so the "fixed" confirmation landed right back inside the file it was steered away
  from, corrupting the header exactly as before. The same collision happens either way
  round when a shell merges both streams into the export file, as `2>&1` does
  (`export-csv /dev/stdout > out.csv 2>&1` or the `/dev/stderr` equivalent) — there,
  the confirmation has no channel left that is not the file itself. `export-csv` and
  `reqs-export` now check, for each export, which of your two terminal streams (if
  either) is actually the file you asked for, and print the one-line confirmation to
  whichever stream is NOT that file. In the one case where both streams turn out to be
  the same file, no confirmation line is printed at all, because there is nowhere
  honest left to put it. Every other destination — an ordinary file, a path, a plain
  redirect of only one stream — is unaffected; this changes only where the one-line
  confirmation goes for these two specific shapes, including "nowhere" in the last one.
- **A read-only MCP call no longer waits on — or fails behind — someone else's write
  (CB-195).** Opening a connection always re-seeded two tables (the merge-lock row, the
  four default milestones) with an unconditional insert, even though the row was
  already there from the very first time the tracker was created. SQLite still took a
  write lock for that guaranteed no-op, so any call opening a fresh connection while
  another session was mid-write could sit out the full five-second wait — and, under
  longer contention, fail outright with "database is locked" for a call that never
  needed to write anything. Both seeds now check first and write only when the row is
  genuinely missing, so the ordinary case never touches the write lock at all.
- **The tool-call counter now skips a row under long contention instead of delaying
  your call (CB-192).** Recording how a tool call went used to share the same
  five-second write-lock budget as everything else, so under sustained contention the
  bookkeeping itself could hold up the response you were waiting for — and, at the far
  end of that wait, still lose the row it was trying to record. The counter's own
  connection now gives up after about 50ms (six times the slowest write this project
  has measured in normal use) and drops the row rather than the call: you get your
  answer promptly, and only pathological contention (a wedged writer, an unusually long
  competing transaction) costs you one line of `codebugs usage` history, reported to
  stderr exactly as any other recording failure already is.
- **A tracker whose database file is read-only can now be READ, not just refused
  (CB-195, CB-199).** This falls directly out of the same fix above: opening a
  connection no longer writes anything on the ordinary path, so a `stats` or `query`
  call against a read-only tracker file now succeeds instead of refusing outright —
  before, a read-only tracker refused every single command, reads included, because
  the removed unconditional insert happened to fail first and take the whole
  connection down with it. Writing to a read-only tracker is, correctly, still
  refused; the one narrowing worth knowing about is that the refusal message for that
  specific case is currently a raw error rather than the clean one-line explanation
  other unwritable-tracker cases give you (tracked as CB-199) — the command still
  fails, still writes nothing, just with a less friendly message.

## [0.2.0] — 2026-08-25

The first cut since 0.1.0, mostly about the tracker telling the truth. Repeat reports
of one defect now land on one card that keeps up with them: a later, worse report
raises its severity, new tags join it, category spellings stop forking. The backlog
gained answers you used to assemble by hand — what changed since a date, grouping by
tag or metadata key, how cards cite and split from each other, and where a card's code
went after a rename. Most of the rest is commands that stopped claiming unearned
success: a failed export no longer destroys the one it replaced, a broken import leaves
nothing behind, dropped filters filter again, tracebacks became one line. A CSV export
is finally a backup, and an agent connecting over MCP is told the recommended loop. Not
here: requirements got fixes but no new capability, and the way a finding is identified
was not re-versioned, so nothing forces you to migrate. One clean-up is worth running on
an older tracker even so: both corpus-wide passes still write only under `--apply`, and
until `categories-normalize` has folded yours, reporting a defect that was filed before
category spellings were canonicalized can open a second card instead of raising the
count on the one you already have. The tracker in this repository has been folded once;
yours has not, unless you ran it yourself.

**One thing to change in your own scripts.** A single argument became strict, in two
ways: `codebugs add -l/--lines` now refuses an explicitly empty value, and refuses a
value that disagrees with a `lines` key passed inside `--meta`. Both used to report
success while quietly not storing what you typed; both now say what is wrong and exit 1.
The two entries are marked **BREAKING (CLI)** in the `### Changed` sections below, and
each says what to change.

### Fixed
- **`reqs_embed` and `reqs_batch_embed` now refuse a vector that would break search,
  and `reqs_search_similar` no longer fails outright when it meets one (CB-174).**
  Nothing used to check the vectors you store, so a single vector of the wrong size
  — one call with a different embedding model is enough — left similarity search
  unable to work at all until somebody found and removed that vector by hand.

  **What is refused now**, on both the single and the batch tool: a vector that is
  empty, that contains something other than a number, that contains NaN or infinity,
  or that has a different number of components than the vectors already stored in
  this tracker. A batch must also be consistent with itself, which matters in an
  empty tracker where there is nothing else to compare against. Each refusal names
  both sizes, so you can see what the tracker expects.

  NaN was the quietest of these and the most worth knowing about: it stored without
  complaint, and the requirement then dropped out of every search result with no
  error anywhere. A NaN in the vector you *search* with removed every result, so
  "nothing is similar" looked exactly like an empty tracker. Search now refuses that
  query instead of answering it with silence.

  **`reqs_search_similar` skips requirements whose stored vector is a different size
  from your query** rather than failing the whole search on the first one it meets.
  Those requirements are simply not in the results, so **`reqs_embedding_stats` now
  reports which vector sizes the tracker actually holds** (`dimensions`) and whether
  there is more than one (`mixed`) — that is where to look if a search returns fewer
  results than you expect.

  If your query vector's size matches *nothing* stored, the search says so instead of
  handing you an empty list, since an empty list there would be indistinguishable from
  "nothing is similar". Searching a tracker with no vectors in it still returns an
  empty list, which is the honest answer.

  **One limit, stated plainly: there is still no way to change embedding model.**
  This package has no operation that clears and re-computes existing vectors, so once
  a tracker holds vectors of one size, vectors of another size are refused and there
  is no sanctioned path around that. The refusal message says so. If you need this,
  say so — it was deliberately not built ahead of somebody asking for it.

  **Also documented, and now enforced by a test rather than merely promised:** the
  embedding tools never receive your requirement's text, and no part of codebugs
  sends anything anywhere — you compute the vector yourself and codebugs keeps it in
  its own local database. The tool descriptions say this, and a check refuses any
  network capability being imported into the package.
- **Adding a requirement (`reqs_add`) now always returns exactly what you just wrote
  (CB-117).** It used to write the row, then read it back from the database in a
  separate step; in the rare case another request changed or removed that same
  requirement in between, you could get back someone else's version instead of your
  own, with nothing in the response to warn you. It now returns the written row
  directly, so what you see is always your own write.
- **The mutation-testing scripts under `tests/manual/` (`mutate_cb69.py`, `mutate_cb31.py`)
  now refuse to run on a dirty tree instead of risking your uncommitted work (CB-173).**
  Each script writes a broken version of a source file to disk, runs the suite, then
  restores the original — that restore is correct when the run finishes normally. The
  danger was a run that got killed or timed out first: the broken version was left
  stranded on disk, and cleaning it up with `git checkout --` discarded any uncommitted
  edits on that file too. They now check `git status` on the files they're about to touch
  and stop with a clear error naming them before writing anything. Commit or stash first,
  or pass `--allow-dirty` (or set `CODEBUGS_MUTATION_ALLOW_DIRTY=1`) if you really mean it.
- **A leaked tool-call tail at the end of a `description` is now cut at the write
  boundary, and `add`/`batch_add` say so in the response (CB-90).** Some filing agents
  include a slice of their own XML-like tool call in the value they pass as
  `description` — the authored prose ends on a finished sentence and an envelope of
  `<parameter name="…">` lines follows it. Nothing on the write path rejected that, so
  the junk was stored, shown to every later reader, and fed to the similarity scorer.
  Measured on a peer tracker of 3373 rows: 80 descriptions carry it, from one source,
  spread over 24 separate days across four months — repeated filer behaviour rather
  than one broken run.

  **The tail is cut, not refused, and the cut is never silent.** A filer that cannot
  repair itself would lose the entire finding on a refusal, and the finding is real —
  only its tail is junk. So `add` and `batch_add` responses now carry a top-level
  **`stripped_description_tail`** boolean, present on every branch, where `False` means
  *checked, nothing to cut* and never *no such channel* — the same discipline as the
  existing `stripped_meta_keys` and `attention` keys. `True` means the text stored is
  not byte-for-byte the text you passed.

  **The cut happens before the fingerprint is derived**, which is the point rather than
  a detail: `description` is an identity input, so a tailed and a clean report of one
  defect used to hash apart into two cards. They now collapse onto one.

  On the **command line**, where the response is not shown, `codebugs add` prints a note
  to stderr instead, for the same reason: the text that reached the database is not the
  text you typed, and you have to be told.

  **Prose that merely quotes the marker is left alone.** The predicate is not the
  `</description>` marker by itself: this project's own card describing this bug quotes
  that marker three times legitimately, and a marker-only rule would have destroyed most
  of it. A cut happens only where the closing tag is *unmatched* (a balanced XML snippet
  inside a card is not touched), *everything* after it is envelope, and every one of
  those lines begins with `<` at column zero — which is what separates a terminal leak
  from a quotation or an indented code block. Anything else is left visible rather than
  cut on a guess; the measured cost of that choice is one row of the 80, whose tail is a
  bare newline with no envelope at all.

  **One case is not solved and is named rather than hidden:** a card that *ends* on a
  verbatim, unindented, unfenced quotation of the leak is cut, and what it loses is the
  evidence it exists to record. At that point the quotation is byte-identical to the
  thing it quotes, so no rule reading only the text can tell them apart. Both ordinary
  ways of quoting markup already avoid it — put the sample in a fenced code block, or
  indent it — and the response key and the stderr note mean you are told when it happens
  and still hold the text you sent.

  **Scope:** the observation path only. An explicit `finding_id` asserts identity and
  bypasses this exactly as it bypasses deduplication and category normalization; CSV
  import is unchanged (an import is not an observation); `update` is unchanged, where
  `description` is immutable by design. Nothing rewrites descriptions already stored —
  cleaning an existing corpus is a separate, deliberate operation.

- **The `Args:` section of every MCP tool description now reaches your client as a
  Markdown list instead of collapsing into one run-on paragraph (CB-156).** 73 of the
  83 tool descriptions carry a Google-style `Args:` section, three carry `Returns:`,
  and none of them was marked up as Markdown — while MCP clients render descriptions
  as Markdown. `Args:` sits at column 0 with its argument lines indented four spaces
  and no blank line between, which CommonMark reads as a paragraph followed by *lazy
  continuations*: the indentation is stripped, the line breaks become spaces, and every
  argument fuses into a single line with the boundary between one argument and the next
  gone. Descriptions now carry a real bullet list, so each argument is its own item —
  including the 28 descriptions with an argument whose text wrapped onto a second line,
  which now folds into that argument's own bullet rather than drifting off.

  **The basis for fixing this is not "it is broken for everyone", and saying so
  precisely matters.** A client configured with GFM-style hard line breaks
  (`breaks: true`) shows the lines separately and never saw the defect at all. The
  basis is that the old text was **correct only under a particular setting of somebody
  else's renderer** — a real Markdown list renders correctly under both settings, which
  removes the dependency on a foreign configuration.

  **Only the markup changed.** Not one word of any description was added, removed or
  reordered, and no tool's parameters or behaviour moved. One `Returns:` section
  (`codesweep_add`) is deliberately untouched, because its body is a sentence rather
  than a list of arguments, and turning prose into bullets would invent a structure the
  text does not have.

- **An unknown `--by` on `codebugs stats` now prints one line instead of a traceback,
  and no longer leaks its database connection (CB-170).** The handler called the domain
  function bare and closed the connection on the following statement, outside any
  `finally`, so a rejected axis escaped as a raw traceback while the sibling `query`
  verb had both halves closed long ago. It matters more now than it did: the set of
  values `--by` accepts has just grown a `meta:<key>` form, so typing an axis the tool
  refuses is a thing a reasonable person will do. **Scope, stated because the rest is
  real**: only this handler was fixed. A sweep of the remaining handlers for the same
  shape stays on CB-170.

- **`codebugs add` now records the revision the card was filed at (CB-144).** A card
  filed from the CLI used to store `reported_at_commit = NULL` forever: the automatic
  HEAD capture existed only in the MCP `add` / `batch_add` tools, and the CLI handler
  did not reach it. The consequence was not cosmetic — the location anchor is keyed on
  that frozen commit, so a CLI-filed card could not be anchored by any channel, and the
  ceiling on anchor coverage was set by which surface you happened to file from.
  `codebugs add` now captures `git rev-parse HEAD` in the directory you run it in,
  exactly as the MCP tools already did; outside a repository the column stays NULL
  rather than acquiring an invented value. **Nothing else changed**: `import-csv` still
  never stamps the local HEAD onto another tracker's rows, and `restore` still puts back
  whatever the export carried, `NULL` included. There is still no `--commit` flag —
  supplying one by hand is a separate, already-tracked gap (CB-6).

### Added
- **`codebugs usage` shows which tools get called and what fails (release-b, DIR-1).**
  Summarizes per-tool call counts, failure counts, and total/average duration, sorted
  by call count. **It counts only calls made through the MCP server** — not the
  `codebugs` CLI, and not a direct import of `codebugs.findings` or any other module —
  and says so in its own output every time it runs. Failures are recorded by exception
  CLASS only, never by message, so a caller's own data never ends up in the counter.
- **The MCP server now tells a newly-connected agent the recommended working loop, and
  `codebugs --help` names a first step.** `MCPServer(instructions=)` describes the loop —
  file an observation, read `attention`/`dedup_action` before acting on it, close when
  fixed — and says dedup is the point, not a side effect, and to claim a card before
  working on it alongside other agents. `codebugs --help` gained a short "Getting
  started" line pointing at `init`, `add` and `query`.
- **`query` and `stats` can now group by TAG and by a top-level `meta` key (CB-62).**
  The tracker could group findings only by the five columns of the row, while the axes
  a corpus is actually dense along live in `tags` and `meta` — measured on this tracker
  on 2026-08-25, 160 of its 172 cards carry a tag and 155 carry at least one
  non-machinery `meta` key, across 387 distinct tags and 313 distinct keys. (Those are
  measurements of a moving corpus, not invariants; they are stamped so a later reader
  can tell a stale number from a wrong one.) `--group-by tag` / `--group-by meta:<key>`
  (and `stats --by`) answer
  those, and the point is COMPOSITION: `grouping-tags` has been counting tags for a
  while, but it takes only `status` and `category`, so "which tags do the critical open
  cards in this file carry" could not be asked at all. The two tools deliberately
  overlap and their descriptions now say so — one is a tag census with pair
  co-occurrence, the other a distribution that composes with every `query` filter.
  Their totals are pinned equal by a test, because two shipped tools disagreeing about
  one corpus is the failure this was built to avoid.

  **These two axes do not partition the population, and the answer says so out loud.**
  A card with two tags is counted under both; a card carrying no value on the axis is in
  no group and would otherwise vanish from the result without trace. Every grouped
  response — on the five old axes too — now carries `population`, `ungrouped_rows`,
  `multi_group_rows` and `nonscalar_value_rows` beside `groups`, and the CLI prints them
  as a footer. Zero is reported as loudly as forty: "no cards without tags" and "forty
  cards without tags" are different facts, and a line that appeared only when it was
  non-zero could not state the first.

  **A `meta` key containing `.`, `[`, `]` or `"` is REFUSED rather than guessed at.**
  SQLite reads `$.a.b` as a nested lookup, so on `{"a.b": 1, "a": {"b": 2}}` it answers
  2 — the wrong value, silently. Two of this tracker's 313 keys carry a dot and cannot
  be grouped by until the naming grammar is settled; that is a real cost and it is
  preferred to a wrong answer. The two existing `meta_key` FILTERS build their path the
  same naive way and are deliberately left alone, because the population relying on that
  behaviour is not measured — the asymmetry is tracked as CB-167 and the refusal message
  names it. A key that is absent, JSON null, or holds an object or array is reported as
  ungrouped, never invented.

  **One guard is deliberately looser than canonical JSON, and the reason is that
  this package writes non-canonical JSON itself.** A row is skipped only when its
  `tags`/`meta` cannot be parsed at all — but `NaN` and `Infinity` are rejected by
  RFC-8259 and *written by `json.dumps` by default*, so `add_finding(meta={"x":
  float("nan")})` stores `{"x": NaN}` and that value is explicitly supported
  (CB-82). A canonical check would have hidden such a row from every `meta:` axis
  — including for a neighbouring key holding an ordinary string — while
  `grouping-tags` went on counting it, which is precisely the two-tools-one-corpus
  divergence this feature exists to prevent. The parse check therefore accepts
  what SQLite's own `json_extract`/`json_each` accept, and no more. A meta key
  containing a control character is refused alongside the path metacharacters:
  SQLite's path is a C string, so a NUL truncates it and the key `a\0b` would
  silently read its neighbour `a`.

- **An ordinary read of a card now says where its code went (BT-7).** Until now the
  location anchor was visible only through `anchor-resolve` / `anchor_resolve` — a
  second, deliberate call that nobody makes while simply reading a card. `get` (MCP and
  CLI) now carries an `anchor` summary and RESOLVES it by default, so a card whose file
  has since been renamed reports the new path in the answer to the question you actually
  asked. `query` carries the same summary but does **not** resolve it by default, and
  the asymmetry is deliberate rather than an oversight: resolving one anchor costs two
  to four git calls, and `query` is the primary read path with a page of up to a hundred
  rows. Pass `resolve_anchors` (MCP) / `--resolve-anchors` (CLI) to resolve a page, and
  it is resolved in ONE pass — the per-repository work is done once for the whole page,
  never per row. `get` has the mirror-image escape hatch, `resolve_anchors=False` /
  `--no-resolve-anchor`, for a read that must not spawn a process at all.

  **A card that carries no anchor costs nothing, structurally.** Such a row is not sent
  to the resolver and returns quickly; it never reaches it. The same is true of a card
  whose capture LOOKED and had nothing to grab — on a live tracker that is the majority
  of the anchored population, so it matters that it is free too.

  **The summary distinguishes the answers a reader needs to keep apart**, where before
  there was one absent key: `absent` (no anchor was ever captured), `retracted` (the
  `loc: null` tombstone — someone said "do not anchor this"), `refused` (capture looked
  and found nothing to anchor to, with the token saying why), and `anchored`, which
  additionally reports `loc_status`, `moved_file` and the current `path`. A stored object
  that breaks its own invariants is `invalid`, and a `meta` column that does not parse at
  all is `unreadable`. `moved_file` is `null` rather than `false` when nothing was
  resolved: "did not move" and "was not looked at" are different answers.

  `staleness_check` carries the summary too, with the same opt-in `resolve_anchors`. The
  two answer neighbouring questions and are more useful together than apart: `file_status`
  says what became of the FILE, the anchor says where the reported LINES are now.

  Under all of this is a new registry in `db.py`, `register_read_enricher` — a member of
  its family and the first READING one, so it carries none of the writing seams'
  transaction machinery. `loc` registers itself into it, exactly as `similarity` registers
  into the pre-add resolver seam, and core never learns an extension's vocabulary. An
  enricher that fails cannot take down the read it was decorating, and its failure is
  reported IN THE RESPONSE (`{"state": "unavailable", "error": …}`) rather than only on
  stderr — a silently missing key would be indistinguishable from "this card has no
  anchor", which is the exact conflation this whole design exists to end. That stamp is
  built by the extension itself, so it has the SAME shape as a real summary — a narrower
  failure object would make every consumer special-case exactly the path meant to be
  survivable.

  `recent` carries the summary too, always unresolved and with no flag: it answers "what
  changed", not "where is it", and a key present on `query` but absent on `recent` would
  teach a reader to test for presence. `query(group_by=…, resolve_anchors=True)` is
  REFUSED rather than silently ignored — a grouped result carries counts, not findings,
  so nothing there could honour the argument (CB-28).

- **`anchor-recapture` can now take rows that never carried a location anchor at all
  (BT-7).** Anchor capture happens only when a genuinely new finding is filed, so every
  card filed before that landed carries no anchor — and the repair pass could not reach
  them: it skipped any row without one before it ever looked. `--include-unanchored` on
  the CLI verb, `include_unanchored` on the MCP tool, widens the population to exactly
  those rows. They are reported under their own outcome, `would_backfill` / `backfilled`,
  and deliberately **not** folded into `would_update` / `updated`: "how many cards
  acquired an anchor for the first time" is the number this exists to produce, and added
  to "how many anchors were refreshed" it stops answering.

  **Applying it stays a decision you make by hand.** As before, and as with the category
  retro-fold, the pass is a **dry run by default** — without `--apply` no write
  transaction is opened at all. This change ships the ability to see the number, not a
  migration that runs itself. Nothing has been applied to any tracker.

  **Three things it deliberately is not.** It is not a fingerprint backfill — a
  fingerprint is a card's identity and re-keying is a separate, negotiated operation;
  nothing here reads or writes that column. It is not `--force-tombstone`: a `loc: null`
  tombstone means "do not recapture", it is a value someone wrote on purpose, and this
  flag never touches it — merging the two would have made "take the rows nobody anchored"
  the way to erase tombstones. And it does not weaken the existing guarantees: a failed
  capture still never replaces a valid stored anchor, and a row whose anchor changed
  while the pass was reading git is still left to whoever wrote it.

  **A row whose `meta` column does not parse is counted, not touched** — reported as
  `unreadable_meta`, because writing an anchor into it would rewrite a column the pass
  could not read, and "eleven rows I refused to touch" is a different answer from
  "nothing to report".

- **The "fingerprint is held by a live card" refusal is now countable (BT-8).** When you
  re-open a `wont_fix` / `not_a_bug` card whose fingerprint a live recurrence already
  holds, the refusal is unchanged — same error, still naming the card that blocks you.
  What is new is that each refusal increments `meta.fingerprint_refusals` on the card
  you tried to re-open, so `query(meta_key="fingerprint_refusals")` lists the cards
  people keep trying to bring back, and how often. No new command and no new tool.

  **That read is MCP-side today.** The CLI `query` verb exposes no `--meta-key` — it
  never has, for this key or for `category_minted` and `similar_to` before it — so from
  a shell the number is reachable only through the MCP tool. That is a pre-existing gap
  in the query surface (the CB-6 axis), left alone here deliberately rather than widened
  in passing.

  **Read the number with its predicate attached**: it counts refusal EVENTS, not people
  and not intentions — a script retrying in a loop adds one per attempt. It exists to
  measure whether the dedup fork is actually being hit often enough to justify a merge
  policy, which is the question CB-46 is waiting on. The count is deliberately best-
  effort: if it cannot be written, your refusal still arrives unchanged, and it is
  skipped entirely when `update` is called from inside another operation's transaction,
  because recording a statistic must never commit somebody else's unfinished work. The
  key is yours to correct through the MCP tool `update(meta_update={"fingerprint_refusals":
  N})` — where a colliding `notes=` does not fight it, since `meta_update` merges last and
  wins — and it cannot be pre-set when filing a card. **From a shell there is no repair
  path**: the CLI `update` verb carries no `--meta-update` flag, and never has. That is the
  same CB-6 axis gap as the missing `--meta-key` two paragraphs up, declared in
  `SURFACE_GAPS` rather than left to be discovered, and it is stated here as a limit and
  not as something in flight. Re-opening a card does **not** count as a touch: `updated_at` does not
  move, so `codebugs recent` is unaffected.

- **`grouping.py` is now exposed — three MCP tools and three CLI verbs (CB-127).**
  `citation_report`, `tag_report` and `filing_report` shipped (see the
  "`grouping.py` — the three grouping axes the tracker stored but could not query"
  entry below) but were reachable from neither surface. MCP: `grouping_citations`,
  `grouping_tags`, `grouping_filing`; CLI: `codebugs grouping-citations`,
  `grouping-tags`, `grouping-filing` (every domain parameter as a dashed flag, plus
  `--json`). The wrappers are thin forwards — parameter names, types and defaults
  match the domain functions one-to-one, no SQL and no logic in between — and each
  tool description carries the module's own caveats (read-only annotation of what
  people wrote; hubs become anchors; lineage is traversed against the whole tracker).
  `--hub-degree none` reaches the domain's `hub_degree=None` (raw components), so the
  CLI has no hole against MCP. `grouping` is also a `--mode` of its own on both the
  server and the CLI.
- **`recent` — "what closed since &lt;date&gt;" in ONE call, on both surfaces (CB-123).**
  MCP tool `recent`, CLI verb `codebugs recent --since DATE [--status ...]`, domain
  function `findings.recent_findings`. The answer used to be assembled by hand out of a
  ledger file and git history; three consumers needed it, and the net-delta count of
  open cards could not be computed at all without it.

  **The tool says out loud what it actually measures, and that half matters more than
  the first.** It reads `updated_at` — the time of the LAST WRITE to the row — and
  there is no close timestamp anywhere in the schema. A status change moves it, and so
  do a re-tag, a meta patch, a severity re-triage, an `append_note`, and a
  **deduplicated observation**: a repeat report bumps the occurrence count and stamps
  `updated_at` while the status stays exactly where it was. So
  `recent(since=…, status="fixed")` means *cards that are fixed NOW and were touched
  since that date*, **not** *cards closed since that date*. The error is **one-sided** —
  false positives are possible, misses are not, because closing a card always writes
  `updated_at`. That caveat is in the MCP description and in `codebugs recent --help`,
  not only in the card, and two tests pin it as behaviour rather than as prose.

  `since` is **required and inclusive** (`>=`), accepted as `YYYY-MM-DD` or
  `YYYY-MM-DDTHH:MM:SSZ`, and anything else — `""`, `0`, `[]`, `None`, `2026-02-31` —
  is refused with a `ValueError` before a single row is read, rather than defaulted:
  the query-side "empty means no filter" convention is exactly wrong for a mandatory
  window, where a silent widening answers a question nobody asked. `status` keeps the
  ordinary convention (`None`/`""` = every status, aliases resolved); `query`'s
  `deferred` pseudo-status is **not** accepted here and is refused rather than ignored.

  Rows come back newest touch first, with `rowid` breaking the whole-second ties
  `utc_now()` guarantees — the same tiebreaker `_match_fingerprint` already uses, so a
  paged walk is stable. **`query` is untouched, deliberately**: a separate verb rather
  than a `since`/`order_by` parameter, so `query`'s load-bearing parameter-order hazard
  is never entered and no caller-supplied column can reach an `ORDER BY`.

- **`add` and `batch_add` responses now carry an `attention` block (BT-5).** Dedup was
  silent: a serious divergence between an observation and the stored card was reported
  only by burying it in the body of the response, so an automated filer had no
  top-level place to look. `attention` is a list on every response — **always present,
  frequently empty**. Empty means "evaluated, nothing serious fired", which is a
  different fact from "no such channel", so the key is unconditional, exactly like the
  `was_new` and `dedup_action` keys beside it.

  The first record form is `{"signal": "severity_escalated", "from": …, "to": …}`, on
  the `bumped` and `reopened` branches only: it says THIS observation raised the card's
  stored severity. That fact was genuinely unrecoverable before — severity is monotonic
  under observation (CB-52) and the insert path writes no ring entry, so the
  pre-escalation value existed nowhere in the response. Neither insert branch
  (`created`, `recurrence_of_closed`) emits it, for a structural reason rather than a
  policy one: nothing was bumped, so no stored severity was raised. There is no
  de-escalation record, because de-escalation does not exist.

  **Audience: MCP only.** The CLI prints fixed one-line summaries and does not
  serialize the response, and there is no batch verb there at all. **CSV import is
  unaffected by construction** — it reads the internal outcome directly and returns
  counters, so it never reaches the response constructor; an opt-out flag would have
  been dead code and was deliberately not added.

- **A second `attention` record: `{"signal": "category_divergence", "observed": …,
  "stored": …}` (BT-5).** It says this observation does not NAME the category of the
  card it matched, and it appears on every branch that HAS a matched row — `bumped`,
  `reopened`, and `recurrence_of_closed`, where the comparison is against the dismissed
  twin rather than the row just filed. **Both sides are normalized**, so a difference of
  spelling (`Process Improvement` vs `process-improvement`) is deliberately not a signal
  while a difference of name is, and the reported values are the normalized ones — not
  what the caller passed, and not what sits in the column. A stored category that is not
  text (SQLite's dynamic typing permits it on a legacy or explicit-id row) is skipped
  rather than raising, the same policy the category gate already applies for the same
  reason: one odd legacy row must not brick every later observation.

- **`codebugs categories-normalize` — fold the stored corpus onto canonical category
  spellings, so an old card stops forking when it is reported again (CB-61).** New
  findings have had their category spelling canonicalized at write time since CB-60,
  but the rows already in the tracker kept the spelling they were filed with — and a
  finding's identity fingerprint is *stored*, not recomputed, with the category as one
  of its inputs. So re-reporting a pre-CB-60 defect **using the very spelling the card
  carries** derived a different fingerprint than the one on disk and filed a duplicate
  instead of bumping the occurrence count (CB-113(a)). This command closes that: it
  rewrites the stored categories to canon and re-derives the affected fingerprints, in
  one pass, once.

  **It reports by default and writes only with `--apply`.** A dry run prints exactly
  which rows would be renamed, which fingerprints would be re-derived and what would
  go wrong; it opens no write transaction at all. `--fold-map '{"Old Spelling":
  "canonical_name"}'` folds only the spellings you name (every target must already be
  canonical); with no map, every stored spelling folds to its own normalized form.
  `--json` prints the raw report. The MCP tool is `categories_normalize`.

  **What it deliberately does not touch.** The occurrence ring
  (`meta.occurrences`) is never rewritten — those records are verbatim evidence of
  what was observed, variant spelling included. A **caller-supplied** fingerprint is
  left byte-identical (its meaning belongs to whoever supplied it) and so is a `NULL`
  one; only derived `auto:v1` fingerprints are re-keyed, and only after the row's
  stored inputs are verified to still reproduce its stored fingerprint. A row that
  fails that check is skipped **whole** — its category is not rewritten either — and
  reported under `unverifiable`, because renaming without re-keying is precisely the
  identity desync this command exists to remove.

  **If the fold would give two live findings the same identity, nothing is written
  at all** and the report names the fingerprint, the row ids and their old/new
  categories. Merging two cards is a decision, not a migration step. The whole
  `--apply` run is a single transaction, so a stop — or any failure — leaves the
  tracker exactly as it was, and the command exits 1 so a script cannot read the
  refusal as success.

  This is the one sanctioned re-key of a stored fingerprint; `update` still treats
  `fingerprint` as immutable.

### Documentation
- **`add` and `batch_add` now document all four `dedup_action` values (CB-118).** Both
  descriptions named at most two of them, and `batch_add`'s named none — while the one
  they omitted, `recurrence_of_closed`, is the value a client is most likely to
  mishandle: a recurrence of a dismissed twin files a NEW row and reports
  `was_new: true`, so a client that tells create from match by gating on
  `was_new == false` misses the event entirely. The remainder is stated honestly too:
  the dismissed twin's id is always in `meta.recurrence_of` and its status is usually
  in `meta.similar_to`, but on a caller-supplied fingerprint whose text does not
  resemble the twin — or a description below the similarity minimum — the status is
  not in the response at all and costs one `get`.
- **The dedup freeze of `source`, top-level `meta` and `reported_at_ref` is now a
  declared contract, on every reader (BT-4).** No behaviour changed. `source` stores
  the FIRST reporter — later observations' sources live only in the occurrence ring,
  and an imported observation's ring source can be a peer tracker's. Top-level `meta`
  is the row's authored state — a re-observation's meta lands only as per-occurrence
  ring evidence, and `query(meta_key=)`/`meta_value` read the authored column.
  `reported_at_ref` is observation-frozen but manually mutable by design via
  `update(reported_at_ref=)` (a release is tagged after filing); `query(ref=)`
  matches the first-observed or manually assigned ref exactly, never the ring.
  Declared in the MCP tool descriptions (`add`, `update`, `query`, `stats`) and the
  domain docstrings, pinned by prose↔code and behaviour tests.

### Fixed
- **`codebugs add --meta` now REFUSES a malformed payload instead of printing a Python
  traceback (CB-132).** `--meta 'not json'` used to end in a `json.JSONDecodeError`
  traceback, and `--meta '[1,2]'` — valid JSON of the wrong shape — in
  `TypeError: cannot convert dictionary update sequence element #0`. Both now print one
  line naming `--meta` and the offending text (and, for a well-formed non-object, the
  type it parsed as) and exit 1. Nothing is written on either path: the parse happens
  before the tracker is opened, so a refusal costs no partial work. An empty
  `--meta ""` is likewise a supplied-but-empty document rather than an omitted
  argument, and is refused the same way — see the `-l ""` entry under Changed for why
  an empty string is not an absent one on a write path. Unchanged, deliberately: a
  `json.JSONDecodeError` coming from a *stored* row with corrupt metadata still
  surfaces as a crash, because that is data corruption rather than bad input and must
  not be disguised as a usage error.
- **`query(commit=)` now sees the occurrence ring, not only the first report (CB-128).**
  The `reported_at_commit` column is frozen at first report by design (CB-53); every
  re-observation of a deduplicated card records its commit only in
  `meta.occurrences[*].reported_at_commit` (CB-43). The filter read the column alone, so
  "what was observed on commit X" silently missed every card first reported elsewhere and
  re-observed on X — a success-shaped empty answer. The filter is now a disjunction: the
  column OR any ring entry, same prefix match, same hex validation, one row per card even
  when both branches match. Two guards keep garbage on an UNRELATED row from aborting the
  whole query (measured, SQLite 3.47): `json_valid(meta)` evaluated inside a `CASE` (whose
  branches are documented-lazy, unlike `AND`), and `json_each.type = 'object'` against a
  ring element that is not an observation record — so this branch is strictly more robust
  than the existing `meta_key` filter, which still raises on malformed meta. Semantics are
  deliberately **any observation**; `staleness_check` keeps reading the **newest** ring
  entry because it answers a different question (how stale is the card), and both
  docstrings name the divergence. Not closed here: the CLI `query` verb has no `--commit`
  flag at all, so this reaches MCP and library callers only (the CB-6 surface gap).
- **Four public entry points no longer report success about a write that may not have
  happened (CB-87, CB-125, CB-126).** `bench.delete_run`, `bench.delete_benchmark`,
  `embeddings.store_embedding` and `sweep.archive_sweep` each read first — an existence
  check, a COUNT, a `run_id` snapshot, a `_resolve_sweep` lookup — and wrote after, with
  no transaction spanning the pair and a return value that asserted the outcome rather
  than reading it. A concurrent writer fits in that window; `busy_timeout` cannot help,
  because it serializes the writes and never touches the read that preceded them. Each
  body now runs inside `db.txn` (which takes the write lock BEFORE the read) and each
  return value is derived from what the statement actually did — a DELETE's `rowcount`,
  an UPDATE's `RETURNING`. Their own `conn.commit()` calls are gone, so calling any of
  them inside a caller's transaction no longer commits the caller's unrelated work.

  **`bench.delete_benchmark`'s window cost data, not just an inaccurate report.** Results
  are deleted by a snapshotted `run_ids` list while the runs are deleted unconditionally
  by `benchmark`, so a run imported into the window kept its results and lost its
  `codebench_runs` row — orphaned rows referencing a run that no longer exists.
  Reproduced in a test before the fix.

  **One observable contract change, in `embeddings.store_embedding`.** A requirement
  deleted concurrently used to come back as `{"stored": True}` over an UPDATE that
  matched zero rows; it now raises `KeyError`, the same exception (and message) a
  missing requirement has always produced. Nothing changes for a requirement that
  exists. A second, narrower shift is declared rather than left to be discovered: with
  existence now decided BY the UPDATE, a call that is wrong in BOTH ways — unknown id
  AND an unpackable vector — raises `struct.error` from the pack where it used to raise
  `KeyError`; either argument alone is unaffected.

  The counts reported by `bench.delete_run` / `delete_benchmark` are likewise now the
  number of rows the DELETEs removed rather than a number read beforehand. **On a quiet
  database these are the same value, so no test can discriminate that half of the change
  while the transaction holds** — it is defence-in-depth against the read and the write
  disagreeing, and saying so is better than claiming it is covered. What the tests do
  pin is the transaction: each of the four carries a probe that drives a competing writer
  into the window and fails without it.
- **A post-add hook that keeps the finding dict no longer watches response-only keys
  appear on it (CB-119).** Hooks were handed the very dict the response constructor
  then mutated, so a hook storing the reference for later would observe `was_new` and
  `dedup_action` materialize on "its" copy after the call returned — a latent
  cross-layer aliasing bug that `attention` would only have made easier to hit. The
  response is now built as a shallow copy, taken AFTER the hooks have run: a hook's
  own mutations still reach the response, only the aliasing is gone, so no hook
  behaviour changes. A ratchet additionally asserts that no response-only key can ever
  collide with a `findings` column.
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
- **BREAKING (CLI): `codebugs add -l ""` now refuses instead of quietly doing nothing
  (CB-133).** An explicitly typed empty `-l/--lines` used to be dropped on the floor:
  it stored no `lines`, was not treated as a conflict with `--meta`, and the call
  reported success — so a script whose variable came back empty recorded a finding
  that silently lacked the line range it thought it had passed. It now prints one line
  naming `-l/--lines` and exits 1. The rule behind it: on a write path an empty string
  is a value you typed, not an argument you omitted, so only omitting the flag means
  "no line range". Passing a real value (`-l "10-20"`) is unaffected, and so is leaving
  the flag out.
- **BREAKING (CLI): `codebugs add` now REFUSES a `-l/--lines` value that disagrees with a
  `lines` key inside `--meta`, instead of silently storing only the `--meta` one
  (CB-129).** If your script passes both spellings — for example
  `codebugs add -l "1033-1035" --meta '{"lines": [1033, 1035], ...}'` — that call used to
  succeed and store `[1033, 1035]`; the `-l` value went nowhere and nothing said so. It
  now prints both values, says which one would have won, and exits 1.

  **How to fix a call that starts failing: delete one of the two spellings.** Keep
  `--meta '{"lines": ...}'` and drop `-l` if you want the structured value stored (that is
  what was actually being stored before, so keeping it changes nothing about your data);
  keep `-l` and remove the `lines` key from `--meta` if you want the range string. Making
  the two EQUAL also passes — equal values were never a conflict.

  Nothing else changes: `-l` alone still stores its string, `--meta` alone still stores its
  value, and `--meta` keys that no flag writes are untouched. There is no MCP equivalent of
  this argument, so MCP clients are unaffected.

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
