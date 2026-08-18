# Cluster: CB-88 + CB-89 — `file_status` answers for a path it never resolved

Branch `fix/cb-88-cb-89-file-status-path-resolution`, base `2774e16`.
**Draft 3.** Drafts 1 and 2 each failed adversarial review x2 (Opus adversary + Codex/Sol in
parallel); the review history is the appendix, and the fixes are in the body. CB-89's scope was
**ratified by the user on 2026-08-18**: honest scoping only. The deferred capability is **CB-91**;
two defects found during review are **CB-92** (rename parser quoting) and **CB-93** (the
project-root vs cwd contract).

## Why one tree — and the predicate that does NOT hold

**Conceded (review S4): `bug-clustering.md` predicate 2 is NOT satisfied.** The two edits use
different git commands, different conditions and different reason vocabularies. Predicate 1 fails
(neither edit alone fixes the other's card) and so does predicate 4 (either lands alone harmlessly).
They share one tree by **explicit user instruction** ("…in one batch"), which outranks skill policy,
plus one engineering fact: both rewrite the same prologue, so landing them serially means the second
branch rewrites lines the first just moved. Stated as a scheduling decision, not a predicate. The
edit table stays at 2 rows against a ceiling of 4; the hostage test passes — neither member carries
an open decision.

## Root cause

`file_status` has one implicit premise: *`file` is a blob path this repo can resolve, spelled the
way git spells it.* Nothing enforces it and the tracker never promised it — directory-valued,
trailing-slash, glob, absolute-cross-repo and prose values are all in deliberate use. Every branch
downstream is a verdict about a question nobody asked:

- `:97-101` an empty `git log` → **`current`**, for a path git has never heard of;
- `:118` `stat.S_ISREG(...)` → False for a directory that exists, skipping `modified` at `:127`,
  matching no rename at `:158`, falling into the unconditional `deleted` at `:168`;
- `:87` / `:135` run git in the tracker's repo for a path that belongs to another one, and report
  the failure as a provenance failure *of the finding*;
- `:88` passes the raw value as a **pathspec**, so `:` and `:(exclude)…` are read as magic.

Third route to a false `deleted`, matching CB-88's own count: CB-79 (swallowed subprocess failure)
and CB-85 (`isfile` swallowing `OSError`) are commented in place at `:105-125` and `:150-156`. Each
closed one door; this supplies the missing premise. (CB-92 is the fourth, filed separately.)

## The decisive mechanic: derive git's spelling from the physical path, never from the input

Both reviewers, in round 2 and independently, killed draft 2 the same way: **git canonicalizes the
name it prints.** `./a/b`, `a//b` and `/abs/repo/a/b` all come back as `a/b`, and `.` returns
children rather than a record named `.`. So matching git's output against the caller's spelling
turns valid `current` files into `unknown` — the same regression draft 1 had, wearing a new reason
string.

The fix is to stop comparing spellings. The function already computes a physical candidate for
`os.stat`; that candidate is the single source of truth, and its repo-relative form is exactly what
git prints:

```
joined    = os.path.join(cwd, file_path)              # already how stat resolves it
base      = os.path.basename(joined)
candidate = os.path.realpath(joined) if base in ("", ".", "..") \
            else os.path.join(os.path.realpath(os.path.dirname(joined)), base)
inside    = os.path.commonpath([realpath(root), candidate]) == realpath(root)
rel       = os.path.relpath(candidate, realpath(root))     # "." means the worktree root itself
kind      = <type field of `git --literal-pathspecs ls-tree -z --full-tree <commit> -- <rel>`>
```

Measured, one pass, every case (throwaway repo, git 2.53):

| `cwd` | `file` | resolved | kind at commit |
|---|---|---|---|
| root | `pkg` | `pkg` | `tree` |
| root | `pkg/` | `pkg` | `tree` |
| root | `./pkg` | `pkg` | `tree` |
| root | `pkg//mod.py` | `pkg/mod.py` | `blob` |
| root | `/abs/repo/pkg/mod.py` | `pkg/mod.py` | `blob` |
| root | `.` | *the root* | tree, with no git call |
| root | `pkg/*.py`, prose, `:` | — | not known at this commit |
| root | `/etc/hosts` | — | out of repo |
| `pkg/` | `mod.py` | `pkg/mod.py` | `blob` |
| `pkg/` | `..` | *the root* | tree, with no git call |

`--full-tree` is now **correct**, because `rel` is root-relative by construction; draft 1 paired it
with a cwd-relative input and that was fatal. `--literal-pathspecs` neutralizes pathspec magic on
`ls-tree` **and** on the existing `git log` — measured, without it `git log -- ':'` returns the whole
history. `-z` additionally suppresses `core.quotePath` C-quoting (measured: `ümläut.py` raw with
`-z`, `"\303\274ml\303\244ut.py"` without), which is the hazard CB-92 records for the rename parser
that still lacks it.

`ls-tree` is chosen over `git cat-file -t <commit>:<path>` deliberately: the `<rev>:<path>` syntax
re-parses the value as a revision — measured, prose produced `fatal: Not a valid object name
<sha>ontext-mode…`, the leading `c` eaten.

## Plan — the order, and what must not move

Steps marked *(unchanged)* keep today's code verbatim. That is load-bearing: two of draft 1's four
FATALs came from moving code that was already right.

1. *(unchanged)* no `reported_at_commit` → `unknown` / `no_provenance`.
2. *(unchanged)* no cwd → `unknown` / `no_cwd`.
3. **NEW — refuse only what is inherently unanswerable.** An **exactly empty** value →
   `unknown` / `empty_path`; a value containing a **NUL** → `unknown` / `invalid_path`, refused
   *before* any syscall because `os.stat` and `subprocess` raise `ValueError`, which is outside this
   module's `(SubprocessError, OSError)` guard. **Whitespace is preserved** — review measured that
   `git log -- ' '` exits 0 and that whitespace-only filenames are legal POSIX paths, so draft 2's
   "strip and refuse" was refusing valid input on a false premise.
4. **NEW — scope, and its ordering exception.** Resolve the worktree root with
   `git rev-parse --show-toplevel`, build `candidate` as above, and decide containment with
   `os.path.commonpath([root, candidate]) == root` — component-aware, because a naive
   `startswith` admits `/x/repoEVIL` next to `/x/repo` (reproduced). Outside → `unknown` /
   `out_of_repo`. The worktree root itself is **inside** (`rel == "."`).
   - Resolving only the **parent** physically keeps a tracked symlink in scope (git can answer about
     its blob) while refusing an in-repo symlinked *directory* that escapes
     (`<repo>/etcout/hosts` → `/etc/hosts`, reproduced).
   - **Absoluteness is not the criterion**; an absolute path inside the repo keeps working.
   - **Ordering:** this step runs *before* `cat-file` **only when the value is absolute**, and after
     it otherwise. Reason: `cat-file` is the first git call today, so `tests/test_provenance.py:460`
     pins "git missing → `unreachable_commit`"; putting a probe in front of it unconditionally
     rewrites that contract (review F3, reproduced). An absolute path's scope is decidable without
     reference to the commit, and absolute values are the entire measured CB-89 population — this is
     what makes CB-2831 (foreign path *and* foreign commit) report `out_of_repo` rather than
     `unreachable_commit`.
   - The root is resolved **once per `check_findings` batch** and threaded through a private
     `_repo_root=` keyword with three states: `None` = not supplied, resolve it yourself; a string =
     the resolved root; `_ROOT_UNRESOLVED` = the batch already tried and failed, so do not retry.
     Without the third state a failed probe inverts the cost claim into one `rev-parse` per finding
     (review W1). It is private on purpose: a public knob that, when passed wrong, disables the only
     resolution of the boundary is not an API.
5. *(unchanged)* `git cat-file -t <commit>` → `unknown` / `unreachable_commit`.
6. *(unchanged except `--literal-pathspecs`)* `git log <commit>..HEAD -- <file_path>` → `unknown` /
   `git_error` on failure. The pathspec stays the **raw input**, cwd-relative, exactly as today —
   changing it is CB-93, deliberately not this card.
7. **NEW — an empty log no longer means `current`.** Ask for the kind at the reported commit:
   known (`blob` / `tree` / `commit`) → `current` with today's reason text; not known →
   `unknown` / `not_in_commit`. This kills the free-text lie, the glob, and the untracked-regular-
   file lie Codex found. It needs **no stat**, which is why the stat does not move — draft 2 moved
   it above the log and thereby turned a correct `current` into `unknown` / `stat_error` under an
   unreadable parent directory (reproduced).
8. *(unchanged position and guards)* `os.stat`. `OSError` → `unknown` / `stat_error`;
   `FileNotFoundError` / `NotADirectoryError` → absent. CB-85's comment stays.
9. *(unchanged)* regular file → `modified`.
10. **NEW — directory branch.** Kind at the reported commit: `blob` → fall through to
    rename/deleted (a blob that became a directory has lost its blob); `tree`, `commit` (submodule)
    or not-known → `modified`, since `git log` already proved something under it changed in range.
11. **NEW — an existing path that is neither regular nor a directory** (fifo, socket, device) →
    `unknown` / `unsupported_path_kind`, per CB-88's prescription. Today: `deleted`.
12. *(unchanged)* rename lookup → `renamed`; else → `deleted`.

`ls-tree` runs **at most once** per call (7 and 10 are mutually exclusive) and never on the busiest
path. The `file_status` vocabulary is deliberately **not** extended: still
`current | modified | renamed | deleted | unknown`. Only `reason` gains values (`empty_path`,
`invalid_path`, `out_of_repo`, `not_in_commit`, `unsupported_path_kind`). CB-88 floated a new
`directory` status; refused — a directory's `git log` already answers, and a new status value is a
surface change every consumer must learn.

**Every new subprocess call catches `(subprocess.SubprocessError, OSError)` and degrades to
`unknown` / `git_error`, never to "not known at the commit".** Collapsing a git refusal into
`kind = None` would make step 7 answer `not_in_commit` and step 10 answer `modified` on the strength
of a question that errored — this module's own "a guard reporting clean because it could not look"
failure, for the fourth time.

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Refuse what this repo cannot answer for, before asking git about it: refuse empty/NUL, resolve the worktree root, decide containment component-wise | `provenance.py` — new `_repo_root()`, `_resolve_candidate()`, steps 3-4, `_repo_root=` threading from `check_findings` | CB-89 (ratified scope) | `pytest tests/test_provenance.py -q -k "out_of_repo or empty or nul or symlink"` |
| 2 | Never assert a verdict from an unasked question: literal pathspecs, kind-at-commit gating both `current` and the directory branch | `provenance.py` — new `_kind_at_commit()`, steps 6-11, plus the `staleness_check` docstring and the wire golden | CB-88, and the free-text / untracked / glob / magic lies | `pytest tests/test_provenance.py -q`; regenerate and diff the golden |

## Surface change

`staleness_check`'s docstring enumerates the unknown reasons as "(no provenance data, unreachable
commit)" (`provenance.py:378-379`) and that text ships to MCP clients — it is stored verbatim in
`tests/golden/mcp_schema.json`. Five new reasons make it wrong, so the docstring is updated and the
golden regenerated with
`PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json` (a bare `python`
from this worktree would snapshot main's tree). There is **no** CLI surface: `provenance.register_cli`
registers `resolve-trailers` only.

## Existing tests this change touches — all three, named

1. **`tests/test_provenance.py:556-564::test_a_directory_where_a_file_is_expected_is_not_regular`**
   asserts a directory does **not** report `modified`. That assertion is the bug CB-88 was filed
   for. Inverted, with a docstring recording that CB-85's actual concern — an `OSError` must not
   become a verdict — is still pinned by the `stat_error` test.
2. **`tests/test_provenance.py:402-435`** injects `PermissionError` at the **third**
   `subprocess.check_output` and its docstring says the first two must succeed. Adding
   `--literal-pathspecs` does not change the count, but the `ls-tree` call does on some paths, so
   the injection is **re-keyed on argv** (fire on `git diff --diff-filter=R`) instead of on call
   index. Without this the CB-79 guard stays green while pinning a different branch — vacuous,
   exactly the `TestKnownLimits` failure CLAUDE.md records (review F4, reproduced).
3. **`tests/test_provenance.py:460-467::test_a_missing_git_still_degrades_exactly_as_before`** pins
   "git missing → `unreachable_commit`". Preserved by the step-4 ordering exception above; a test is
   added for the absolute-path case, where a failed root probe legitimately reports `git_error`.

## Verification

**Class A — must fail against `git show main:src/codebugs/provenance.py`.** Every "main today" value
is **measured when the test is written, not predicted** — review found two predicted values wrong
(`:` is `deleted`, not `modified`; a plain fifo is `current`, not `deleted`, because it is
untracked). Where a row needs a specific fixture to be reachable, the fixture is part of the row.

| row | fixture note |
|---|---|
| directory, no trailing slash | — |
| **directory, trailing slash** | the majority population — see below |
| glob-valued `file` | — |
| free-text `file` | — |
| untracked regular file, empty log | — |
| `:` as `file` (pathspec magic) | — |
| empty `file` | — |
| `file` containing NUL | must not raise |
| absolute path outside the repo | — |
| foreign path **and** foreign commit | the CB-2831 shape |
| in-repo symlinked dir escaping to `/etc` | — |
| fifo | **tracked, then replaced by a fifo** — a plain fifo is untracked and reads `current` |
| submodule directory | gitlink SHA must advance in range (review S3 moved this row out of Class B) |
| `pkg/..` resolving to the worktree root | main says `deleted` for a path that is the repo itself |

**Class B — green on both sides, each docstring saying so and why.** Four pin behaviour draft 1 or
draft 2 would have broken and are worth their weight: an **absolute path inside** the repo still
`current`/`modified`; an unchanged file under a `chmod 000` parent still `current`; a genuinely
deleted file still `deleted` when **cwd is a subdirectory**; a symlinked repo root still answers.
Two more — a blob→directory swap never `modified`, an unmerged-branch path never `deleted` — are
green against every design considered and are labelled as **contract pins, not regression pins**
(review S4: claiming all six discriminate was an overstatement).

**The population that justifies the trailing-slash row**, measured on
`/home/faxik/w/autosorter/.codebugs/findings.db` (3211 findings): **155 findings carry a
trailing-slash `file` across 51 distinct paths, 71 of them live** — `dashboard-ui/` (48),
`src/autosorter/` (13), `tests/` (6). CB-88's census counted 47 directory-valued cards *without* a
slash. Draft 1 would have fixed the 47 and missed the 155.

Also run: `uv run --extra dev python -m pytest tests/ -q` in the worktree;
`uv run --extra dev ruff check src/ tests/`; the reproducer re-run and diffed line by line.

**Outcome, measured on the real tracker** (1215 live findings, read-only, `/home/faxik/w/autosorter`):

| bucket | main | after |
|---|---|---|
| `deleted` | **51** — every path verified present on disk; the one absent is the glob `src/autosorter/core/steps_*.py`, which matches 8 real files | **0** |
| `current` | 300 | 234 — the 66 lost were "unchanged since" claims about paths not in the reported commit |
| `modified` | 696 | 746 — the directory-valued cards, answered instead of buried |
| `renamed` | 3 | 3 |
| `git_error` + `unreachable_commit` | 7 | 0, now reported as `out_of_repo` — exactly CB-89's measured population |

The `deleted` bucket — documented as "the strongest deterministic dismissal signal available" and
sized as a backlog-compression lever — was **100% false positive** and is now empty. 53 tests in
`tests/test_provenance.py`, 1458 in the suite, `ruff` clean.

## Risks, costs and conceded limits

- **Deliberate behaviour changes, all stated:** a path not known at the reported commit with no
  commits in range → `unknown` / `not_in_commit` instead of `current`; an existing non-regular
  non-directory path → `unknown` instead of `deleted`; a path outside the worktree →
  `unknown` / `out_of_repo`; empty and NUL values → their own reasons. **Every one moves a verdict
  out of a confident bucket into `unknown`; none moves anything into `deleted`.**
- **Cost — predicted a ~35% subprocess increase, measured a 2× speedup.** Review W3 reasoned from
  call counts that the `current` and directory paths each gain one `ls-tree`. True, and the
  conclusion was still wrong, because it counted calls rather than timing them: the paths that used
  to fall through to `git diff --diff-filter=R -M` were paying for a **whole-repo rename detection**
  over the whole range, and those are exactly the paths that now answer earlier. Measured end to end
  over the real autosorter tracker (1215 live findings, 1063 distinct `(file, commit)` pairs, same
  machine, the *fixed* run first so the *old* one had the warmer cache): **27.1s → 14.0s**. Written
  down because a cost claim derived from counting is a prediction until something is timed.
- **CB-88c corrected.** Draft 1 claimed a path that was a blob and is now a directory "stays
  `deleted`". Measured: when content survives the move git reports `R100 swap.py swap.py/inner.py`
  and the function returns **`renamed`** — before and after. The invariant that is pinned is that
  such a path never reports `modified` or `current`.
- **Bare repositories:** `rev-parse --show-toplevel` exits 128 there. The root is unresolvable, so
  scope cannot be decided and the function degrades to `git_error` for absolute values and to
  today's behaviour otherwise. Not supported, stated rather than discovered.
- **Not fixed, filed:** CB-92 (rename parser lacks `-z`/`core.quotePath=false` — a fourth route to
  a false `deleted`), CB-93 (`file` documented project-root-relative while every operation is
  cwd-relative), CB-91 (the cross-repo capability, ratified deferral, plus the `--tracker-root`
  vs ambient-cwd divergence Codex found).
- **Must not regress:** CB-89's observation 2 — a file living only on an unmerged branch. Its blob
  is known at the reported commit, so step 7 returns `current` and it can never reach `deleted`.

## Appendix — Adversarial Review x2, two rounds

**Round 1 (draft 1): FAIL, four reproduced FATALs.**
Corroborated by both models: the trailing slash defeats the discriminator (Codex found the
mechanism, Opus measured the 155-finding population and showed the fix hit the minority); no defined
degrade for the new git calls; every absolute path refused, including in-repo ones; lexical-vs-
physical containment unspecified.
Codex only: pathspec magic (`:` returns the whole history through today's `git log` — a live defect
in existing code); the untracked-regular-file false `current`, which draft 1's "skip the tree lookup
for regular files" optimization preserved; submodule gitlinks; the rename parser's quoting hazard
(→ CB-92); per-finding `rev-parse` cost (→ one per batch).
Opus only: the `--full-tree` coordinate mismatch, reproduced turning a **correct** `deleted` into
`unknown` from a subdirectory cwd; the existing test at `:556-564` that must be inverted; the
stat-reorder regression under `chmod 000`; that draft 1's "every test written red first" was false
for five of its own tests; that the clustering justification satisfied no predicate.
**Rejected after measurement:** Codex claimed `ls-tree --full-tree` still resolves pathspecs
relative to a subdirectory cwd. It does not — `--full-tree` makes them root-relative, which is
*why* draft 1's pairing was wrong and why draft 3 pairs it with a root-relative `rel`.

**Round 2 (draft 2): FAIL, and the fatal was found independently by both.**
Both: git canonicalizes the emitted name, so exact-name matching against the caller's spelling
regresses `./x`, `a//b` and every absolute in-repo path from `current` to `unknown`. Fixed by
deriving `rel` from the physical candidate — the mechanic above.
Both: the root probe placed before `cat-file` breaks `tests/test_provenance.py:460`'s missing-git
contract → the ordering exception in step 4.
Codex only: whitespace-only values are legal paths and `git log -- ' '` exits 0, so draft 2's
"strip and refuse" was built on a false premise, while a NUL still escaped as `ValueError`; a
trailing `..` slipped through `commonpath`; bare repositories undefined.
Opus only: the injection index in the CB-79 guard test (`:402-435`) making it **vacuous** after a
new subprocess is added — caught nothing but would have shipped green; two wrong "main today"
baselines in the Class A table; the submodule row misfiled as Class B; two Class B rows that
discriminate against no design.
