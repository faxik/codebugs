# Inventory: CLAUDE.md lines 875-897 (### Database section, DB discovery through CB-24 population)

L876 | RULE | db.connect() walks up from the current working directory looking for an existing .codebugs/ directory.
L876 | RULE | If no tracker is found on the walk, db.connect() raises db.DatabaseNotFoundError.
L876 | IDENT | init_project is the package's single os.makedirs call.
L876 | RULE | init_project is the only function that creates the .codebugs/ directory itself.
L876 | RULE | A .git/ directory stops the upward walk so that a submodule cannot hijack the parent repository's tracker database.
L876 | RULE | A .git file (as in a git worktree) is followed via its gitdir:/commondir pointer, so worktrees resolve to the main repository's database.

L877 | RULE | What counts as "a tracker" differs depending on how the root was reached (walk vs. named/declared route), and this asymmetry is intentional, not an oversight (CB-23).
L877 | RULE | On the walk route, the .codebugs/ directory itself is the opt-in: finding one that holds no findings.db causes the database to be created inside it.
L877 | RULE | On a named or declared root (project_dir/--repo, --tracker-root, $CODEBUGS_ROOT), the findings.db file itself is what counts as "the tracker".
L877 | RULE | A declared/named directory found without a database file fails closed, naming which channel supplied the bad root.
L877 | WHY-NARROW | Standing in a directory is evidence about where the user is, while a named path is an assertion that can be stale, inherited by an unrelated subprocess, or mistyped — this is why a silent second empty tracker (CB-8) is the specific danger on the named/declared route.
L877 | IDENT | CB-8 is the card for the "silent second empty tracker" failure mode.
L877 | HISTORY-LOADBEARING | _db_path's own docstring already promised the named branch would refuse on a missing tracker, but the actual code checked only os.path.isdir — the fix exists to make code match the doc's pre-existing promise, not to invent a new one.
L877 | RULE | init_project creates the .codebugs/ directory before creating the database file inside it, so a Ctrl-C'd init leaves exactly a directory with no database.
L877 | RULE | The walk route self-healing that half-initialized state (directory, no db) is the correct behavior, not a bug to fix.
L877 | RULE | The creation rule must be stated precisely at the call site, not as a general slogan.
L877 | IDENT | _open(path, create=...) is the only function in the codebase that opens a database connection.
L877 | RULE | Exactly two callers pass create=True to _open: init_project (directory just created) and connect on the walk route (directory already there).
L877 | RULE | Neither of the two create=True callers invents a directory that was not already present.
L877 | HISTORY-LOADBEARING | The flat statement "init_project is the only creator" is false — connect also creates (the database file) by design; an earlier draft of this document asserted both halves of that contradiction two sentences apart, which is the exact failure this documentation practice exists to avoid.
L877 | IDENT | tests/test_db_infra.py::TestOpenCallSitesRatchet pins the create=True call-site count so a third creating caller cannot appear quietly.
L877 | REPEAT | Described as "the same shape as the BEGIN IMMEDIATE allowlist" (see L891, db.txn as sole executable BEGIN IMMEDIATE, counted by tests/test_claims.py::test_24).
L877 | HISTORY-LOADBEARING | The two-caller carve-out exists because init used to create its database indirectly by calling connect, so an earlier tightening of the resolver broke the one caller that legitimately must create — this history is the reason the current split exists.

L878 | RULE | Discovery of the tracker root is a heuristic, so it has a declared override, and every override channel outranks the plain upward walk.
L878 | RULE | Resolution order in _db_path is: explicit project_dir argument (what --repo passes) first, then --tracker-root, then $CODEBUGS_ROOT, then the walk.
L878 | WHY-NARROW | project_dir/--repo is checked first because it is per-call and therefore beats ambient state such as an environment variable.
L878 | RULE | Entry points call db.set_tracker_root() exactly once.
L878 | RULE | Nothing else besides the entry point may call db.set_tracker_root(), because db is the only place that can honor a declared root.
L878 | MEASURED | ~50 db.connect() call sites pass no arguments — the reason nothing but the entry point can set the tracker root.
L878 | RULE | set_tracker_root() validates nothing at call time — a root may be named before its tracker exists.
L878 | WHY-NARROW | Dying at startup if a declared root has no tracker yet would destroy the lazy-connect self-healing that CB-11 protects, so the validation check belongs at the point of use, not at declaration time.
L878 | IDENT | CB-11 is the card protecting lazy-connect self-healing, cited as the reason validation is deferred to use.
L878 | RULE | A declared root that resolves to no tracker fails closed and names which channel (env var, flag) supplied that root.
L878 | WHY-NARROW | The declared value is ambient (e.g. an export inherited by an unrelated subprocess), so "no tracker there" must never quietly become a second empty tracker — CB-8's failure mode arriving through a new (declared-root) door.
L878 | RULE | A blank/empty value in either the --tracker-root flag or $CODEBUGS_ROOT env var counts as "not a declaration" (i.e. is ignored), the same convention as an empty query filter.

L879 | BOUNDARY | Some directory layouts are provably undiscoverable by the resolver; the chosen honest response is an escape hatch (a declared root), not a better heuristic guess (CB-13).
L879 | RULE | _worktree_main_root accepts any commondir whose basename is literally ".git" — reusing git's own heuristic.
L879 | BOUNDARY | A repo using --separate-git-dir whose git directory happens to be named ".git" misbinds to the admin/git directory instead of the actual checkout (CB-13).
L879 | WHY-NARROW | There is no local discriminator to fix this: git itself reports that admin directory as a valid work tree too, so any local "fix" would just be a different guess, not a correct one.
L879 | RULE | The commondir-basename heuristic is deliberately kept as-is rather than patched.
L879 | IDENT | TestSeparateGitDirMisbinding pins that the CB-13 misbinding still reproduces; if that premise test ever stops failing, the remedy below is moot.
L879 | RULE | The prescribed remedy for the CB-13 misbinding is to use a declared root (--tracker-root / $CODEBUGS_ROOT / --repo).
L879 | WHY-NARROW | General shape: when a rule cannot be decided from local evidence alone, the design supplies external metadata (a declared root) rather than deepening the guess.

L880 | RULE | A binding that cannot be seen is a binding that cannot be debugged — the motivating principle for db.describe_root().
L880 | RULE | db.describe_root() never raises.
L880 | RULE | db.describe_root() reports a dict with keys: root, source, source_label, path, exists, exists_reason, error, writable, dir_writable, unexamined.
L880 | RULE | codebugs where (CLI) and the MCP startup preflight are the ONLY two consumers of describe_root(), deliberately.
L880 | WHY-NARROW | Having exactly one resolver and exactly two consumers means the diagnostic (where) and the server (preflight) can never disagree about where the process is pointed.
L880 | RULE | Every key in describe_root()'s output dict is unconditionally present — an invariant, not an implementation detail.
L880 | RULE | An empty or None value for a key means "the question was asked and there is nothing to say", never "this channel does not exist".
L880 | RULE | Consumers of describe_root() must compare values with `is` (identity), not truthiness.
L880 | RULE | Consumers print `writable` and `dir_writable` only when False, and `unexamined` only when non-empty.

L881 | RULE | The `exists` field of describe_root() is three-valued, and the third value ("undetermined") is the DEFAULT outcome, not an extra case bolted on (CB-203).
L881 | HISTORY-LOADBEARING | os.path.isfile answers a three-valued question with two values because it swallows every OSError the underlying stat() raises, so "could not look" previously came back as "nothing is there" — the story is why a default-to-undetermined design was chosen.
L881 | MEASURED | With the execute bit removed from a .codebugs/ directory (chmod 666), `codebugs where` printed "no database there yet — the next command creates one" at exit 0, over a populated tracker every other verb refused.
L881 | BOUNDARY | This false-positive statement is judged strictly worse than CB-100 and CB-182, where a warning merely went missing (silence) rather than a false statement being printed.
L881 | IDENT | CB-100 and CB-182 are prior cards where a warning merely went missing, contrasted against CB-203's false statement.
L881 | RULE | db._path_state returns "absent" on exactly ONE condition: ENOENT on the name itself.
L881 | RULE | db._path_state returns None (undetermined), with a human reason in exists_reason, on every other way of failing to see a regular file.
L881 | WHY-NARROW | Defaulting to "undetermined" on any unenumerated failure means a mechanism nobody anticipated lands in "could not tell" by construction, rather than needing individual foresight.
L881 | MEASURED | Three ways to break a tracker were known at the start of the unit; measurement found five MORE (a directory, a named pipe, a socket, a symlink loop, a name exceeding the kernel's path-length limit), each with its own errno.
L881 | BOUNDARY | There is no reason to think eight (3 known + 5 found) is the complete population of ways a tracker path can fail.
L881 | RULE | lstat runs before stat, and this ordering is load-bearing.
L881 | MEASURED | os.stat on a DANGLING SYMLINK raises FileNotFoundError byte-for-byte identical to an empty name's error, and there the next command self-heals by creating a database at the link's target.
L881 | WHY-NARROW | Because of that self-heal, a stat-first guard (instead of lstat-first) would have wrongly called the dangling-symlink state "proven absence".
L881 | RULE | The same _path_state predicate replaced isfile/isdir at every site that inspects an ALREADY-RESOLVED root — all of _path_state's callers, not merely the one site the originating card named.
L881 | RULE | The declared (--tracker-root/$CODEBUGS_ROOT) and named (--repo) routes now stay fail-closed and no longer assert an absence they could not establish.
L881 | HISTORY | provenance.py had already made this exact two-valued-to-three-valued swap for CB-85, in a different file, on identical reasoning, before db.py did.
L881 | IDENT | CB-85 is the prior card where provenance.py made the analogous fix.
L881 | HISTORY-LOADBEARING | A later correction (CB-218) found the claim "at all five sites in db.py" true individually but false about the composition: the module as a whole still answered a reachability question with two values in one further, undescribed place — the story is what forces the property to be re-stated at composition width.
L881 | RULE | The property that matters is general, not a count: every question db.py asks about "what is at a path" must be either three-valued, or explicitly declared in an exceptions table with a stated reason, and a test checks which applies.
L881 | IDENT | CB-224 is the card that found the unqualified "is three-valued, full stop" claim false.
L881 | MEASURED | At CB-224, _linked_worktree_gitdir's own (gitdir / "commondir").is_file() call and two reads inside init_project (os.path.isdir/os.path.exists) still answered "what's at this path" with only two values — the identical CB-203 shape recurring a third time.
L881 | MEASURED | The document's own self-reported recurrence counts were themselves wrong and had to be corrected: "three copies" was actually four (CB-24), "five sites in db.py" was actually six (CB-218's own correction), and "every question" still left three readings standing.
L881 | RULE | A universal property merely re-asserted in prose after each fix is "a gate that cannot fire" written as text — the document's own Embeddings section names the same shape for a different claim.
L881 | IDENT | tests/test_two_valued_path_gate.py holds the universal "every two-valued read is routed or declared" property, built on the model of test_no_network_capability.py and test_strict_bool_gates.py.
L881 | RULE | The gate requires every two-valued read in db.py be either routed through _path_state, or named in a self-deleting DECLARED_EXCEPTIONS table with a reason per row.
L881 | RULE | "Self-deleting" means a DECLARED_EXCEPTIONS row naming an import/read no longer present in the source fails the check, so the table cannot become a dumping ground for undetected violations.
L881 | RULE | Two rows currently exist in the exceptions table: init_project's purely informational `created` flag, and _open's CB-86 message-selection branch.
L881 | HISTORY-LOADBEARING | The exceptions-table gate was itself an enumeration of TEXTUAL SPELLINGS — comparing call text against a list of texts, matching except clauses only against the literal name "OSError", and letting one (function, primitive) row license EVERY call of that primitive in that function (CB-227) — this story is the entire justification for the re-keyed predicate below.
L881 | MEASURED | A decisive os.path.exists call restored inside init_project inherited the licence written to excuse the harmless `created` flag beside it — the too-broad key let a real bug hide behind an unrelated declared exception.
L881 | HISTORY | The first mutant test for this used a different primitive (os.path.isdir) than the escape, so the old key happened to catch it — that draft discriminated nothing and was rewritten.
L881 | MEASURED | Measured over TEN bypasses (six from the card's oracle, four from this unit's own sweep), the old (function, primitive) key caught ZERO of ten; the re-keyed predicate catches all TEN; both still catch the two positive controls, and neither flags the unmutated file.
L881 | MEASURED | Separately, forty distinct evasions were swept and run against the new predicate: it catches FIFTEEN, and the misses are individually named in the gate's own docstring.
L881 | RULE | A miss that is explicitly announced costs less than a miss that is silent.
L881 | RULE | A count of how many evasions are missed is deliberately NOT stated as a fixed number in this prose, since such a number would go stale as the evasion list changes.
L881 | RULE | The new predicate resolves a call through the file's own import bindings and compares it as an OBJECT, so `from os.path import isdir`, `import os.path as osp`, `from os import path`, and posixpath/genericpath (literally the same functions, not aliases) all collapse onto one recognized capability.
L881 | RULE | The new predicate judges an except clause via the LIVE class hierarchy (issubclass(caught, OSError) or issubclass(OSError, caught)), so IOError, EnvironmentError, os.error, Exception, a bare `except:`, an `except*` group, and concrete errnos are all covered with no hardcoded alias list, while ValueError and this module's own exception classes are correctly refused.
L881 | RULE | A DECLARED_EXCEPTIONS row is now keyed by (function, primitive, call text), and a row matching more than one call site is REFUSED rather than stretched over both.
L881 | RULE | Scope statement: the gate holds exactly one property — inside src/codebugs/db.py, a call asking "what is at a path" and answering with two values must be fixed or declared.
L881 | BOUNDARY | (1) An indirection hiding the call's NAME — getattr(os.path,"isdir")(p), a primitive held in a dict/tuple, functools.partial, operator.methodcaller, eval — needs value tracking, the same boundary test_no_network_capability.py draws around __import__.
L881 | RULE | A SIMPLE binding (f = os.path.isdir; f(p)) IS caught, because that is name resolution; a conditional or container-held reference is not.
L881 | BOUNDARY | (2) A swallow returning through anything but a literal — `ok = False; return ok`, `return bool(x)`, a flag set in a finally, contextlib.suppress(OSError) — needs data-flow analysis, out of scope.
L881 | BOUNDARY | (3) The same read moved into a sibling module db.py imports is invisible to the gate: it reads one file by decision, and provenance.py keeps its own schedule.
L881 | BOUNDARY | (4, "the semantic sentry") A function can answer three-valued perfectly while its CALLER still reads the undetermined value as "definitely not there" — the meaning lives in the caller, so no predicate over the reading function can ever see it; this is why CB-227 needed a behavioural oracle beside the gate, and the gate must never be described as covering it.
L881 | BOUNDARY | A second, distinct KIND of question — "what does this file SAY" (reading the .git file's gitdir: pointer, and commondir's contents via read_text) — was never in the gate's vocabulary at all, since no stat swap could reach it; both swallowed failure into a confident negative.
L881 | MEASURED | On the CB-224 tree: chmod 000 on a linked worktree's .git FILE made `codebugs where` print "no .codebugs/ found … or any parent" at exit 1 with the unexamined list EMPTY, though the project's real tracker was one hop away.
L881 | MEASURED | The same chmod 000 condition made `codebugs init` create a NEW tracker INSIDE that worktree at exit 0, silently — git deletes it with the worktree, findings and all.
L881 | MEASURED | chmod 000 on commondir made `where` state as fact "and its main checkout has no tracker either" over a main checkout that HELD the tracker and that the process had never located.
L881 | RULE | Both failure modes (.git file, commondir) now return the same (path, detail) third value the discovery route already had elsewhere.
L881 | RULE | _resolve_db no longer asserts anything about a main checkout it could not locate.
L881 | RULE | _enclosing_worktree_root now RETURNS that third value instead of dropping it, which is what lets init_project's already-ratified WorktreeTrackerError fire.
L881 | HISTORY-LOADBEARING | No new policy was decided in making _enclosing_worktree_root return the third value — a refusal that already existed was simply unreachable, and the same drop is why CB-224's original fix closed only the `where` half of its own defect and left the `init` half creating the doomed tracker.
L881 | RULE | A .git whose PATH cannot be examined at all still records-and-continues, per CB-218's already-ratified answer.
L881 | HISTORY | Counts were REMOVED from the gate's own docstring rather than corrected: a claim of "12 except OSError blocks" in db.py was wrong (the AST says 10), and "the two except (OSError, ValueError) pairs _path_state carries" was right on one reading and wrong on another (_path_state carries two; the FILE carries three).
L881 | RULE | A third "corrected" count would rot exactly as the first two did — a number that decides anything belongs in a test, not in prose.

L882 | RULE | The upward walk (_walk_db_root) is ALSO three-valued, and what it does with the third value is the whole decision (CB-218).
L882 | RULE | _walk_db_root asks three questions per directory: is there a .codebugs/, is there a .git directory, is there a .git file.
L882 | HISTORY-LOADBEARING | Path.is_dir()/is_file() swallow the underlying OSError exactly as os.path.isdir does, so "could not look" arrived spelled "not there" in all three questions — the story is why the walk was changed to record-and-continue rather than treat failure as absence.
L882 | MEASURED | On the unfixed tree, with the execute bit off a directory holding the project's tracker and an unrelated .codebugs/ one level above, `codebugs where` printed a clean binding to the stranger at exit 0 with no warning, and `stats` answered about the stranger's empty population.
L882 | RULE | The .git half is the same harm through a second door: an unanswerable .git reads as "no boundary here" and the walk crosses the repository boundary.
L882 | MEASURED | Reproduced in ISOLATED form: repository directory fully readable, its .codebugs/ provably absent, a symbolic-link loop at .git — so nothing else could be blamed.
L882 | RULE | On an undetermined answer the walk RECORDS AND CONTINUES; it does not stop.
L882 | WHY-NARROW | Stopping would turn today's harmless case (one filesystem error on an unrelated ancestor) into a refusal to work at all, judged the worse outcome (false refusal).
L882 | RULE | Continuing is judged safe; staying silent is not, because silence is the entire mechanism by which the measured state became a wrong bind rather than a visible one.
L882 | RULE | Skipped candidates travel out as describe_root()'s `unexamined`; both consumers name them.
L882 | RULE | The "no tracker anywhere" refusal no longer claims an absence about parents nobody could look at, while the refusal on a route that examined everything stays byte-identical.
L882 | HISTORY-LOADBEARING | A truncating version of the human-readable unexamined list was written first and refuted by measurement: one unreadable "wall" makes every question below it unanswerable too, and a cap keeping the first entries kept the wall's shadows while dropping the entry naming the wall itself — the story is why the list is deepest-first and untruncated.
L882 | RULE | The unexamined list runs deepest-first and is not truncated in the human-facing text.
L882 | HISTORY | When CB-218 landed, _enclosing_worktree_root took the same primitive with NO behaviour change (undiscriminable by any test at the time), since it then only chose between two refusal sentences.
L882 | HISTORY-LOADBEARING | CB-239: that "no behaviour change" state is kept in the PAST TENSE deliberately rather than deleted, because CB-227 later made the function RETURN the third value instead of dropping it — while both sentences stood present-tense simultaneously, the document said the opposite of itself two bullets apart and a reader could not tell which half held today.
L882 | RULE | Every claim in this document about live code is written so its as-of validity is visible.
L882 | BOUNDARY | _find_db_root (the thin wrapper that discards the list, feeding init_project's shadow guard and cli's --force variant) is STILL two-valued deliberately: an unexaminable ancestor still lets `init` create a tracker a real one above would shadow — named as the same defect in a different decision, needing its own separate answer.
L882 | BOUNDARY | Residual, named not closed: describe_root reports on the PATH only, never on CONTENTS — a corrupt findings.db still reads writable:True and a clean binding, and the failure surfaces only when a verb opens it.
L882 | IDENT | CB-201 item 1 is the separate card now carrying the content-corruption residual.
L882 | RULE | _writable_probe's docstring names that boundary and the rest of the _is_environmental family it does and does not cover, and declares its own except arm as measured-dead insurance rather than implying coverage.
L882 | RULE | The preflight (server._preflight) is warn-only and must stay that way.
L882 | WHY-NARROW | It writes to stderr because MCP clients log stderr, while tool responses cannot carry a startup diagnostic.
L882 | RULE | The preflight is silent on the ordinary discovered path but announces a declared root, since a non-default binding is what a reader needs to see later.
L882 | HISTORY | Before the preflight existed (CB-11), a misconfigured server looked healthy at startup and failed every call forever with no single moment naming the cause.
L882 | RULE | `exists` is kept separate from `error` because resolving is not the same as being there.
L882 | RULE | On the walk route, a .codebugs/ holding no database resolves cleanly and the next write *creates* the tracker, so nothing errors and nothing is visible.
L882 | RULE | That "resolves cleanly, nothing there yet" state is the CB-13 misbinding's exact shape, since the wrong root there is a stray directory.
L882 | RULE | Both consumers now say so, and that is the one case where the preflight speaks on a purely `discovery` binding.
L882 | RULE | CB-49: a path in a diagnostic is only a report if its coordinate system travels with it.
L882 | RULE | declared_tracker_root() returns the declared value absolutized via a lexical abspath, never a realpath.
L882 | WHY-NARROW | realpath is avoided because a declared root is often deliberately symlinked, and the job is to pin the coordinate system, not rewrite the declaration.
L882 | RULE | Because of the lexical-abspath choice, `where`, the preflight line, and fail-closed error texts all report a root readable without knowing the process cwd.
L882 | BOUNDARY | The one deliberate exception: with a deleted cwd, abspath itself needs os.getcwd() and can fail, so _absolutized falls back to the raw value rather than violate describe_root's never-raises contract.

L883 | RULE | An ENVIRONMENTAL sqlite failure is classified inside _open and raised as a TYPE, never classified at the CLI boundary (CB-86).
L883 | RULE | sqlite3.OperationalError derives from sqlite3.Error → Exception, and NOT from OSError.
L883 | HISTORY-LOADBEARING | Because OperationalError is not an OSError subclass, both CB-71's `open(` sweep and CB-79's OSError widening were structurally blind to it, closing the "CLI crashed at an I/O boundary" class three separate times without anyone enumerating the whole family — the story is why classification was finally centralized in _open.
L883 | IDENT | db._is_environmental is PRIVATE, deliberately, because a caller classifying at the boundary was the rejected design.
L883 | RULE | db._is_environmental keys on sqlite result codes {8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN}, using the same `& 0xFF` mask as db.is_contention.
L883 | WHY-NARROW | The `& 0xFF` mask is load-bearing because a read-only DIRECTORY raises the extended result code 1544, which the mask still matches to base code 8.
L883 | RULE | _open converts a match into db.TrackerUnwritableError, a sibling of DatabaseNotFoundError.
L883 | HISTORY-LOADBEARING | The rejected alternative — a sqlite3.OperationalError arm at cli.main — was refuted by tests/test_bench.py:789, which ratifies the traceback as the discriminator between a post-commit failure and an input error; a central arm cannot tell those apart, and "exit code is unchanged so no new lie is possible" proves too much (it would equally license the central except OSError CB-55 forbids).
L883 | IDENT | tests/test_bench.py:789 is the test ratifying "traceback as discriminator".
L883 | RULE | This constraint is verbatim CB-55's constraint applied to a different exception class.
L883 | RULE | A type raised from _open carries its provenance structurally, because _open always raises before it returns a connection.
L883 | BOUNDARY | The precise claim is narrower than it sounds: it means "this failed while opening a connection", not "nothing was written anywhere"; no handler connects twice today, but that is a property of the call sites.
L883 | RULE | Three raise sites live inside _open, one of them merge.ensure_schema, reached several frames down through the _resolved_order() loop — verified by running it.
L883 | HISTORY-LOADBEARING | That merge.ensure_schema raise site is what once made one classification point suffice even for a read-only database FILE; CB-195 (see L884) changed that, so this claim is stated in the past tense so the two bullets are not read as disagreeing (CB-213).
L883 | RULE | The live/narrowed claim is: one classification point covers only *opening a connection* (see L884 for the full statement).
L883 | MEASURED | SQLITE_PERM (3) is deliberately absent: chmod 000 measured yields 14, and no CLI path was found to produce 3.
L883 | RULE | SQLITE_NOTADB (26) cannot be added because it arrives as DatabaseError, not OperationalError.
L883 | HISTORY | Both absences (3 and 26) were measured rather than reasoned, after a sibling card added three prose-sourced entries in a paragraph congratulating itself for avoiding exactly that.
L883 | RULE | SQLITE_CANTOPEN is ambiguous — identical code and message for a missing file and an unopenable one.
L883 | RULE | os.path.exists is used only to pick the MESSAGE in that ambiguous case, never to change classification.
L883 | RULE | An unreadable parent directory still reads as "not found", stated at the site rather than discovered later.

L884 | RULE | "One classification point suffices" is NARROWED, and the narrowing is CB-195's own doing (tracked open as CB-199).
L884 | HISTORY-LOADBEARING | merge.ensure_schema was one of the three raise sites specifically because it ran an UNCONDITIONAL write on every db.connect(), by accident and not by design — that accident was the mechanism that made a read-only DATABASE FILE fail inside _open()'s classification on every verb, including a pure read.
L884 | RULE | CB-195 made that write conditional on the seed row being missing, which is the whole point of that fix.
L884 | WHY-NARROW | A purely reading db.connect() must never take the write lock merely to attempt a redundant insert.
L884 | MEASURED | On the unfixed tree, `stats` on a chmod 444 tracker file used to refuse at exit 1 with a clean message — a read-only tracker could not even be READ.
L884 | MEASURED | On the fixed tree, the same `stats` call now succeeds — a genuine capability gained, not a defect.
L884 | BOUNDARY | The cost sits on the write side only: once seed rows already exist (the ordinary case), _open() attempts no write of its own, so a read-only file is no longer detected at connect time.
L884 | BOUNDARY | A subsequent WRITE verb against a read-only file still refuses at exit 1 with nothing landed, but the failure now surfaces from the domain's own INSERT, outside _open()'s try/except, as a raw Python traceback instead of the clean one-liner.
L884 | RULE | Precise claim: one classification point covers opening a connection; a write failing on an already-open connection to a read-only file is discovered at the write itself, not covered by that point.
L884 | IDENT | tests/test_db_unwritable.py::TestTheFourShapesEndToEnd::test_B_read_only_database_file pins this three-way shape (read succeeds; write refuses cleanly; only the message narrows).
L884 | IDENT | CB-199 is the open card for reconciling the two.
L884 | BOUNDARY | Rejected/deferred fix: any write-based probe restoring early detection would have to attempt a write on every db.connect(), reintroducing exactly the write-lock contention CB-195 removes — so this is not a two-line fix and stays open by design.

L885 | HISTORY-LOADBEARING | Every docstring on this fix qualifies its promise with "steady state", and nobody had measured the other side of that phrase until CB-202 — the measurement itself is what grounds the boundary stated below.
L885 | BOUNDARY | While a seed row is MISSING (first open of any tracker, or one whose seed rows were removed), the insert really does run, and a reading db.connect() waits out a concurrent writer exactly as before.
L885 | MEASURED | 734ms wait against a 700ms foreign hold when seed rows are missing, versus 0.8ms once the rows exist.
L885 | RULE | This wait happens once per tracker; no read-first rule can avoid it, so it is a boundary rather than a residual defect.
L885 | IDENT | src/codebugs/db.py's _open comment carries the same narrowing note at the site, instructing not to widen it back.

L886 | RULE | The ratchet holding this keys on a PRIMITIVE, and its first version did not (CB-202).
L886 | IDENT | tests/test_db_infra.py::TestSchemaInitRunsNoUncheckedDml asks whether a schema-init function executes a string it did not itself check against the database.
L886 | HISTORY-LOADBEARING | The replaced version asked whether a string literal in a conn.execute() call led with a DML verb and sat under an ast.If; an isolated acceptor defeated both halves at once by moving the same insert into the module's schema CONSTANT, which the existing split/if-truthiness loop then ran unconditionally — the story is what forces the replacement to key on a primitive rather than a literal-in-a-call pattern.
L886 | MEASURED | All three of the old test's assertions stayed green with the defect fully restored by the acceptor's bypass.
L886 | HISTORY-LOADBEARING | CB-213: an earlier version of THIS document's own explanation for why the old rule was defeatable was itself wrong and had to be corrected.
L886 | RULE | The literal-in-a-call clause was never vacuous: merge.ensure_schema, milestones/_schema.ensure_schema and reqs.ensure_schema DO pass string literals straight into conn.execute(), and two of those literals are the very INSERT OR IGNORE seeds CB-195 repaired.
L886 | RULE | Those are named as examples, not a closed set, deliberately — replacing a false universal with an enumeration would be the same defect one edit later.
L886 | RULE | What actually made the old rule defeatable was its OTHER clause ("not nested inside any ast.If"): the old test's premise tests pinned both directions correctly and correctly stopped flagging seeds once CB-195 guarded them with a prior read.
L886 | RULE | The acceptor's bypass blinded the old test a SECOND, INDEPENDENT way, by moving the SQL out of the call entirely into the module's schema constant.
L886 | RULE | Both mechanisms are real; an earlier draft of this document generalized the second onto the first and asserted a false property of the tree.
L886 | BOUNDARY | Keying the old rule on the literal name "ensure_schema" left migration helper functions those functions call — findings._migrate_statuses, reqs._migrate_to_lowercase, sweep._migrate, and others — entirely unread.
L886 | RULE | How many such helpers exist, and how many execute sites they hold, is a question for the ratchet itself, deliberately not for this prose, because both counts that stood here previously went stale.
L886 | RULE | The replacement ratchet resolves SQL text through constants, loops, split/strip, f-string/concatenation prefixes, and executescript bodies; derives entry points from register_schema() calls rather than by name; follows the module-local closure; and accepts a branch only when a READ could have fed it.
L886 | RULE | The replacement ratchet is fail-closed on what it cannot resolve, and its docstring enumerates what it still cannot see.
L886 | RULE | A ratchet whose promise is wider than its check is the defect CB-202 exists to close, so the docstring enumeration is part of the fix, not a caveat.

L887 | RULE | Each domain module defines its schema as a module-level string, either SCHEMA or <DOMAIN>_SCHEMA.
L887 | RULE | Each domain module provides an ensure_schema(conn) function.

L888 | RULE | All schema changes must be additive (new tables, new columns with defaults) or use explicit migration functions.

L889 | RULE | Parameterized queries must be used exclusively; values must never be interpolated directly into SQL.

L890 | RULE | SQLite WAL mode is enabled.
L890 | RULE | db.connect() explicitly sets busy_timeout=5000.
L890 | HISTORY | That timeout used to be inherited implicitly from sqlite3.connect(timeout=5.0)'s default and appeared nowhere explicit in the source; it is now set explicitly.
L890 | RULE | busy_timeout is what turns a losing concurrent writer into a clean rowcount=0 instead of an OperationalError.

L891 | RULE | A plain, bare `BEGIN` statement must never be written.
L891 | RULE | A plain BEGIN pins a read snapshot, and the later write upgrade dies with SQLITE_BUSY_SNAPSHOT, which busy_timeout cannot rescue.
L891 | RULE | Use db.txn(conn) instead, which issues BEGIN IMMEDIATE, saves/restores isolation_level, and is reentrant (yields False and does nothing when a transaction is already open).
L891 | RULE | db.txn is now the ONLY executable BEGIN IMMEDIATE in the package.
L891 | HISTORY-LOADBEARING | Two previously grandfathered raw BEGIN IMMEDIATE sites used to exist outside db.txn; CB-40 removed them — the story is why the ratchet had to change from deduplication to counting.
L891 | IDENT | tests/test_claims.py::test_24 now counts BEGIN IMMEDIATE occurrences rather than deduplicating by (filename, statement).
L891 | WHY-NARROW | The old set-based check would have passed any number of raw sites inside an already-allowed file, and zero as well — it could not distinguish "no raw sites" from "many raw sites in an allowed file".

L892 | RULE | Assigning conn.isolation_level COMMITS an open transaction — the save/restore idiom is not a neutral wrapper (CB-40).
L892 | HISTORY-LOADBEARING | merge.merge and capacity.pull_next both opened with conn.isolation_level = None, so calling either from inside a caller's transaction silently committed the caller's unrelated work before starting its own — the exact inverse of the reentrancy contract every db.txn user advertises.
L892 | MEASURED | Verified by running it: in_transaction goes True → False and a subsequent ROLLBACK finds nothing to undo.
L892 | RULE | Both merge.merge and capacity.pull_next now use db.txn.
L892 | RULE | Both also refuse an ambient transaction outright, with a `raise` and not an `assert` (assert is stripped under -O).
L892 | WHY-NARROW | Each is an acquisition (a merge lock, a work claim); under an ambient transaction db.txn yields False, so they would report success for a row no other connection can see yet.
L892 | RULE | A gate that says "you hold the lock" before the lock is committed is worse than the defect being fixed.

L893 | RULE | A refusal path that writes nothing needs no rollback machinery — and if it does write, reorder it so it doesn't.
L893 | HISTORY-LOADBEARING | The two raw BEGIN IMMEDIATE sites existed because three paths did "roll back and return a value", which a plain `return` inside `with db.txn(...)` cannot do (it commits) — this is why the reordering fix, not a sentinel, was chosen.
L893 | HISTORY-LOADBEARING | An earlier design added a TxnAbort sentinel for this and was rejected in review: db.txn deliberately swallows a failed ROLLBACK (correct, so cleanup never masks the real exception), which would have let a refusal-shaped result come back with the transaction still live.
L893 | RULE | merge.merge now runs its head check before marking a stale holder abandoned, so main_moved has nothing to undo.
L893 | RULE | lock_held and no-candidate never write anything.
L893 | RULE | All three refusal paths simply return and commit an empty transaction.

L894 | RULE | A deadline computed in Python is a defect waiting for something slow to happen — compute it in SQL (CB-41).
L894 | MEASURED | It took THREE review rounds to reach this fix.
L894 | RULE | merge() writes a lease expires_at.
L894 | HISTORY-LOADBEARING | Round 1 sampled the deadline at the top of the function, before the write lock and before the injected git callback; Round 2 moved it below those but left the stale-holder abandoned UPDATE between the sample and the write — both times the lease landed already expired, letting two contenders both get proceed:True.
L894 | RULE | Point-of-use discipline is the wrong enforcement layer, because it must be re-established every time a statement is inserted between sample and write — and twice it silently wasn't.
L894 | RULE | The fix computes the deadline via strftime('%Y-%m-%dT%H:%M:%SZ','now', ?) inside the UPDATE itself, so sampling and writing are one operation.
L894 | RULE | merge.py now imports no clock at all.
L894 | RULE | A comparison timestamp may still be read in Python; reading it early is conservative (an early now makes a lease look live, causing refusal rather than over-grant).
L894 | RULE | Anything written as a deadline goes in SQL.
L894 | RULE | The corresponding test must assert the SQL template, not behaviour, since a Python-sampled deadline still looks fresh unless real time passes during the call — which is why the first regression test passed against the defect it was written for.

L895 | RULE | An idempotency affordance can defeat the gate it sits in front of (CB-41).
L895 | HISTORY-LOADBEARING | merge.merge's "already merging" short-circuit compared only session_id and never read expires_at, while the acquisition path below treated an expired lease as reclaimable — so an expired holder retrying got proceed:True from the first branch while a competitor reclaimed the lease and got proceed:True from the second, defeating the singleton lock without the two branches ever racing.
L895 | BOUNDARY | Every existing test used a fresh lease, where the two branches agree — which is why the existing suite did not catch this.
L895 | RULE | The remedy is renewal: owning the lock renews it regardless of expiry, so expiry no longer decides anything on the self-owned path.
L895 | BOUNDARY | Accepted cost: the TTL still reclaims from a holder that died, but no longer bounds one that is alive, wedged and retrying — that needs liveness detection, not a timestamp.

L896 | RULE | A value computed in Python from a row you just read must be written back inside ONE transaction, and that transaction owns the commit.
L896 | RULE | update_finding / update_requirement merge meta in Python over the row read at the top.
L896 | HISTORY-LOADBEARING | With the read and write in separate statements, two writers both read the stale value, both report success, and the later erases the earlier (CB-24).
L896 | RULE | busy_timeout serializes writes only; it never touches the preceding read, so it cannot help.
L896 | RULE | Wrap the whole body in db.txn, which takes the write lock before the read.
L896 | RULE | Consequence 1: delete the function's own conn.commit().
L896 | WHY-NARROW | db.txn yields False under an ambient transaction, so committing would commit the caller's work too (milestones.triage_dismiss is such a caller and gained atomicity from this change).
L896 | RULE | Consequence 2: convert the returned row AFTER the block.
L896 | WHY-NARROW | row_to_dict raises json.JSONDecodeError on malformed stored meta, and inside the block that would roll back a write the contract promises has landed — the CB-16 lie in a new place; three existing tests caught it.
L896 | IDENT | CB-16 is the origin of the "reporting failure over a landed write" lie class.
L896 | RULE | Consequence 3: a one-statement read-modify-write (SET n = n + 1, or SQL-side json_patch) is NOT an instance of this and needs no wrapping.
L896 | BOUNDARY | The no-op path now holds the write lock for one SELECT.
L896 | HISTORY-LOADBEARING | Deriving "will this write?" from the arguments beforehand was rejected because it duplicates the argument list — the same fragility the lazy-meta guard warns about.

L897 | MEASURED | The CB-24 population is ~19 sites, not the four originally fixed.
L897 | HISTORY-LOADBEARING | CB-24 fixed four sites; CB-27 was filed as "nothing stops a FIFTH" — the story is the direct cause of the sweep described next.
L897 | MEASURED | A mechanical sweep (grep -rn "conn.commit()") found 43 executable sites vs. 7 db.txn users at the time; every committing function was then read.
L897 | MEASURED | The sweep found 19 instances, 13 still unfixed, in blockers.py, merge.py, sweep.py and three milestone modules no card had named.
L897 | RULE | Stated as the repo's recurring lesson for the sixth time: a rule expressed as an enumeration gets fixed at the sites someone enumerated, and the population is always larger than the list.
L897 | IDENT | The outstanding 13 sites are tracked on CB-36 with file:line.
L897 | IDENT | CB-37 (still undecided) would mechanically enforce the rule.
L897 | BOUNDARY | The obvious AST predicate for CB-37 certifies the very bug it was built to catch, and is blind both to reads behind helpers like _get_item_by_ref and to cross-table check-then-act.
L897 | RULE | Consequence 4: read the row RAW inside the block.
L897 | RULE | _get_item_by_ref calls _row_to_item, which json.loads() meta_json before any write.
L897 | RULE | Wrapping such a function without swapping to a non-parsing lookup (_spine._get_item_row_by_ref) leaves consequence 2 unsatisfiable, since there is no state where the write lands and the parse then fails.
L897 | RULE | sqlite3.Row is not a dict — it has no .get().
L897 | RULE | Consequence 5: capture the mutated row with UPDATE … RETURNING *, never re-read after the commit.
L897 | HISTORY-LOADBEARING | release_item re-resolved by item_ref AFTER committing, so a newer attachment inserted in that window was returned instead — reporting status='open' for an item it had just marked done.
L897 | BOUNDARY | The RETURNING fix makes the returned row self-consistent with the row written, but WHICH attachment was selected is still arbitrary — tracked as CB-33.
L897 | IDENT | CB-33 is the card for arbitrary attachment selection.
L897 | IDENT | pull_next has the identical re-read-after-commit window and is tracked as CB-39.
L897 | RULE | A statement that gains RETURNING can never again have its rowcount read.
L897 | BOUNDARY | This forecloses rowcount-based hardening of _decrement_capacity, tracked as CB-38.
L897 | IDENT | CB-38 is the card foreclosed by the RETURNING/rowcount exclusivity.

## VERBATIM-CRITICAL

- `db.connect()` — L876
- `db.DatabaseNotFoundError` — L876
- `init_project` — L876, L877, L881, L882
- `os.makedirs` — L876
- `.codebugs/` — L876, L877
- `.git/` (directory) / `.git` (file) — L876
- `gitdir:` / `commondir` — L876, L881
- CB-23 — L877
- `_db_path` — L877, L878
- `os.path.isdir` — L877, L881
- CB-8 — L877, L878
- `_open(path, create=...)` — L877
- `create=True` — L877
- `tests/test_db_infra.py::TestOpenCallSitesRatchet` — L877
- CB-11 — L878, L882
- `--tracker-root` — L877, L878, L879
- `$CODEBUGS_ROOT` — L877, L878, L879
- `project_dir` / `--repo` — L877, L878
- `db.set_tracker_root()` — L878
- CB-13 — L879
- `_worktree_main_root` — L879
- `TestSeparateGitDirMisbinding` — L879
- `db.describe_root()` — L880, L881, L882
- `codebugs where` — L880, L881
- `source, source_label, path, exists, exists_reason, error, writable, dir_writable, unexamined` — L880
- CB-203 — L881
- `os.path.isfile` — L881
- `db._path_state` — L881, L882
- ENOENT — L881
- CB-100, CB-182 — L881
- `lstat` / `stat` — L881
- CB-85 — L881
- CB-218 — L881, L882
- CB-224 — L881
- `_linked_worktree_gitdir` — L881
- `.is_file()` — L881
- `os.path.exists` — L881, L882
- CB-227 — L881, L882
- `tests/test_two_valued_path_gate.py` — L881
- `test_no_network_capability.py` — L881
- `test_strict_bool_gates.py` — L881
- `DECLARED_EXCEPTIONS` — L881
- `created` flag — L881
- `_open`'s CB-86 message-selection branch — L881
- `issubclass(caught, OSError) or issubclass(OSError, caught)` — L881
- `getattr(os.path, "isdir")(p)` — L881
- `functools.partial`, `operator.methodcaller`, `eval` — L881
- `contextlib.suppress(OSError)` — L881
- `_resolve_db` — L881
- `_enclosing_worktree_root` — L881, L882
- `WorktreeTrackerError` — L881, L882
- CB-201 item 1 — L882
- `server._preflight` — L882
- CB-49 — L882
- `declared_tracker_root()` — L882
- `abspath` / `realpath` — L882
- `_absolutized` — L882
- CB-86 — L883
- `sqlite3.OperationalError` — L883, L884
- `sqlite3.Error` — L883
- CB-71, CB-79 — L883
- `db._is_environmental` — L883
- `8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN` — L883
- `& 0xFF` — L883
- `db.is_contention` — L883
- extended result code `1544` — L883
- `db.TrackerUnwritableError` — L883
- `DatabaseNotFoundError` — L883
- CB-55 — L883
- `tests/test_bench.py:789` — L883
- `merge.ensure_schema` — L883, L886
- `_resolved_order()` — L883
- CB-213 — L883, L886
- `SQLITE_PERM` (3) — L883
- `SQLITE_NOTADB` (26) — L883
- `sqlite3.DatabaseError` — L883
- `SQLITE_CANTOPEN` — L883
- CB-199 — L884
- CB-195 — L884, L883, L886
- `stats` (CLI verb) — L884
- `chmod 444` — L884
- `tests/test_db_unwritable.py::TestTheFourShapesEndToEnd::test_B_read_only_database_file` — L884
- CB-202 — L885, L886
- `src/codebugs/db.py:_open` — L885
- `tests/test_db_infra.py::TestSchemaInitRunsNoUncheckedDml` — L886
- `ast.If` — L886
- `SCHEMA.split(";")` — L886
- `milestones/_schema.ensure_schema` — L886
- `reqs.ensure_schema` — L886
- `INSERT OR IGNORE` — L886
- `findings._migrate_statuses` — L886
- `reqs._migrate_to_lowercase` — L886
- `sweep._migrate` — L886
- `register_schema` — L886
- `SCHEMA` / `<DOMAIN>_SCHEMA` — L887
- `ensure_schema(conn)` — L887
- WAL mode — L890
- `busy_timeout=5000` — L890
- `sqlite3.connect(timeout=5.0)` — L890
- `rowcount=0` — L890
- `OperationalError` — L890
- `BEGIN` (bare) — L891
- `SQLITE_BUSY_SNAPSHOT` — L891
- `db.txn(conn)` — L891, L892, L893, L894, L896
- `BEGIN IMMEDIATE` — L891, L892
- CB-40 — L891, L892
- `tests/test_claims.py::test_24` — L891
- `conn.isolation_level` — L892
- `merge.merge` — L892, L893, L895
- `capacity.pull_next` — L892
- `in_transaction` — L892
- `ROLLBACK` — L892, L893
- `raise` vs `assert` — L892
- `TxnAbort` — L893
- `main_moved` — L893
- `lock_held` — L893
- CB-41 — L894, L895
- `expires_at` — L894, L895
- `strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)` — L894
- `proceed: True` — L894, L895
- `session_id` — L895
- CB-24 — L896, L897
- `update_finding` / `update_requirement` — L896
- `meta` (field) — L896
- `milestones.triage_dismiss` — L896
- `row_to_dict` — L896, L897 (implied)
- `json.JSONDecodeError` — L896
- CB-16 — L896
- `SET n = n + 1` / `json_patch` — L896
- CB-27 — L897
- `grep -rn "conn.commit()"` — L897
- `blockers.py`, `merge.py`, `sweep.py` — L897
- CB-36 — L897
- CB-37 — L897
- `_get_item_by_ref` — L897
- `_row_to_item` — L897
- `meta_json` — L897
- `_spine._get_item_row_by_ref` — L897
- `sqlite3.Row` — L897
- `.get()` — L897
- `UPDATE … RETURNING *` — L897
- `release_item` — L897
- `item_ref` — L897
- CB-33 — L897
- `pull_next` — L897
- CB-39 — L897
- `rowcount` — L897
- `_decrement_capacity` — L897
- CB-38 — L897
