# Cluster: CB-92 + CB-93 — `file_status` carries TWO coordinate systems and mixes them

Branch `fix/cb-92-cb-93-one-coordinate-system`, based on main `7a3bd7c`.
Iteration 4 of the bugfix loop. Focus: `codebugs`, "stabilization batch, one tree".

## Why one tree

**Predicate 1 — same root cause.** CB-88 (landed, `6dba444`) introduced a canonical,
root-relative, physically-resolved path variable:

```python
candidate = _resolve_candidate(cwd, file_path)      # provenance.py:204
rel = os.path.relpath(candidate, root)              # provenance.py:214
```

and then used it in exactly ONE probe, `_kind_at_commit`. Every other operation in
`file_status` still consumes the caller's RAW spelling `file_path`, which is
cwd-relative and uncanonicalized:

| site | line | consumes |
|---|---|---|
| `git log` pathspec | `:252-253` | `file_path` (raw) |
| `os.stat` existence probe | `:294` | `os.path.join(cwd, file_path)` (raw) |
| rename comparison | `:364` | `parts[1] == file_path` (raw) |
| `_kind_at_commit` | `:269, :327` | `rel` (canonical) ✅ |

So the module holds two coordinate systems and picks between them per line. CB-93 *is*
that defect stated as a contract violation; CB-92 *is* that defect arriving at the rename
comparison, plus one genuinely separate parse hazard.

**Falsifiable evidence that this is one cause, not one theme.** `git diff --name-status`
prints **root-relative** paths regardless of cwd — measured, not assumed:

```
$ cd <repo>/pkg && git diff --diff-filter=R -M --name-status BASE..HEAD
R100    pkg/old.py      pkg/new.py          # root-relative, from a subdirectory
$ ... --relative                            # (contrast)
R100    old.py          new.py
```

Therefore `parts[1] == file_path` compares a root-relative string against a cwd-relative
one. From any subdirectory, for any nested path, that comparison **can never match** — and
the fall-through is the unconditional `return {"file_status": "deleted"}` at `:371-374`.
One causal change (make `rel` the only coordinate system) removes that mismatch at the
rename site *and* satisfies CB-93's contract. Neither card needs the other's edit for its
own remaining symptom, which is why the table below has two rows, not one.

**Ceilings.** 2 independent edits (≤4). Both verifiable synchronously by unit tests. No
member waits on a decision: CB-93's decision was **ratified by the user on 2026-08-18 —
option (a), honour the documented root-relative contract.** Hostage test passes.

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Resolve the candidate against the **worktree root** instead of the process cwd, and run every git probe with `cwd=root` so `rel` is the one spelling: `_resolve_candidate(root, …)`, `os.stat(candidate)`, `git log … -- rel`, rename compares `rel`. | `provenance.py` `_resolve_candidate`, the log probe, the stat, both `_kind_at_commit` calls, the rename compare | CB-93; the canonicalization + subdirectory routes of CB-92 | `pytest tests/test_provenance.py -q` |
| 2 | Read the rename probe with `-z`, parse NUL-delimited records, decode with `errors="surrogateescape"`. | `provenance.py` rename probe | CB-92's C-quoting + TAB/newline routes | `pytest tests/test_provenance.py -q` |
| 3 | Sibling sweep: same `errors="surrogateescape"` on the module's other `-z` reader. | `provenance.py` `_kind_at_commit` | none (latent) | `TestPremiseGitRecordShapes` |

**The independence claim is WITHDRAWN — adversarial review falsified it, and the
correction is worth keeping.** The plan asserted "each lands and verifies alone". Measured,
each edit applied alone to a clean copy: **edit 1 alone → 3 failures; edit 2 alone → 6
failures.** The edits are *causally* independent — neither is needed for the other to fix
its own routes, and there is no ordering constraint — but with the hostage tests in the
tree neither leaves the suite green, and "independently landable" has to mean the latter.
The misattribution was structural, not incidental: `TestRenameProbeParsing` is named for
CB-92 yet two of its four non-control tests only pass under edit 1, because those two
routes ARE CB-93's mismatch surfacing at CB-92's comparison. **This lands as ONE unit, one
commit.** That is 1 independently landable edit against a ceiling of 4.

---

## CB-92 — rename probe: confident false `deleted`

### Reproducer (`scratchpad/repro_cb92.py`, run against main `7a3bd7c`)

| case | spelling | expected | got |
|---|---|---|---|
| non-ASCII, git C-quotes its output | `src/ä.py` | `renamed` | **`deleted`** |
| TAB in filename breaks the split | `src/a\tb.py` | `renamed` | **`deleted`** |
| non-canonical spelling (**not on the card**) | `./src/a.py` | `renamed` | **`deleted`** |
| control | `src/a.py` | `renamed` | `renamed` ✅ |

Route 3 involves no quoting at all — it is pure canonicalization, and it is the evidence
that this card and CB-93 share a cause. A fourth route (subdirectory cwd) follows from the
root-relative measurement above.

### Root cause

`:361-369` splits `git diff --name-status` output on `\t` and compares field 1 to the raw
`file_path`. Three ways to miss: `core.quotePath` defaults true so non-ASCII paths arrive
C-quoted (`"pkg/\303\244.py"`, with the literal quotes); a TAB or newline in a name breaks
the field/line split; and the two sides are in different coordinate systems. Every miss
falls to the unconditional `deleted` at `:371`.

### Fix

`-z`, matching what `_kind_at_commit` already does one screen up — and whose docstring
already names this: *"`-z` because it also suppresses `core.quotePath` C-quoting (the
hazard CB-92 records for the rename reader below, which still lacks it)"*.

Measured, `-z` emits raw bytes with NUL field separators, so it fixes quoting **and** the
TAB/newline hazard in one transformation:

```
R100\0pkg/ä.py\0pkg/z.py\0        # -z, raw bytes, NUL-separated
"pkg/\303\244.py"                  # default: C-quoted
```

`-c core.quotePath=false` is deliberately **rejected**: it fixes quoting only, leaving the
TAB/newline route open. Parse defensively by status letter (`R`/`C` take two paths, others
one) rather than assuming `--diff-filter=R` guarantees triples.

### Verification
New `TestRenameProbeParsing`: the three failing routes above become `renamed`; control
stays `renamed`. Each asserted to FAIL against `git show main:src/codebugs/provenance.py`.

---

## CB-93 — the documented contract is the spelling that does not work

### Reproducer (`scratchpad/repro_cb93.py`)

Ground truth: `pkg/mod.py` **was** modified after the reported commit.

| cwd | spelling | result |
|---|---|---|
| repo root | `pkg/mod.py` (documented) | `modified` ✅ |
| `<repo>/pkg` | `pkg/mod.py` (documented) | **`unknown` / `not_in_commit`** |
| `<repo>/pkg` | `mod.py` (undocumented) | `modified` |
| ambient (MCP path, `project_dir=None`) | `pkg/mod.py` | **`unknown` / `not_in_commit`** |

Reachable in production: `staleness_check` passes `project_dir=None` (`:607`),
`check_findings` falls back to `_ambient_cwd()` (`:390`), and `db.connect()`'s walk-up
permits the server to start in any subdirectory.

### Root cause
`findings.py:1392` documents `file` as "File path relative to project root". Nothing in
`provenance.py` honours it; `cwd` is the anchor everywhere.

### Decision — RATIFIED (user, 2026-08-18): option (a)

Honour the documented contract. Evidence put to the user:

- **Population of callers relying on today's behaviour: zero.** Measured across both real
  trackers — 3307 findings, 3086 resolve from the repo root, 213 resolve nowhere
  (prose/globs/deleted paths, already routed to `unknown` by CB-88), 8 absolute.
  **No finding anywhere uses a subdirectory-relative value.**
- **The real cost is a test pin, and it is deliberate.**
  `tests/test_provenance.py:901` `test_a_deleted_file_is_still_deleted_from_a_subdirectory_cwd`
  pins the cwd-relative reading (`file="auth.py"`, cwd `<repo>/src` → `deleted`) and its
  docstring records that a CB-88 review draft broke exactly it. Option (a) rewrites that
  pin. **This is a declared contract change, not a regression** — and it is called out
  here so a future reader does not "repair" it back.

### Fix
Anchor the candidate at `root`, not `cwd`; run every git probe with `cwd=root`. `rel`
becomes the only path spelling that reaches git or `os.stat`.

### Verification
New `TestFileIsRootRelative`: the documented spelling answers correctly from a
subdirectory and via the ambient path. Rewritten `:901` pin: `file="src/auth.py"` with cwd
`<repo>/src` → `deleted`; plus a new assertion that the old cwd-relative spelling now
reports `unknown`/`not_in_commit` — honest, never a confident wrong answer.

---

## Shared risks / out of scope

- **`rel == "."`** (a `file` value naming the worktree root). `_kind_at_commit` already
  special-cases it; with `cwd=root` the log pathspec `.` now means the whole repo rather
  than a subdirectory, so such a card reports `modified`. Existing pin
  `test_a_parent_traversal_landing_on_the_worktree_root_is_not_deleted` (`:839`) still
  holds. Stated because it is a behaviour change nobody asked about.
- **`cat-file` keeps `cwd`** — it takes a commit, never a path, so it has no coordinate
  system to unify. Touching it would be motion, not a fix.
- **Root unresolvable** (bare repo, not a repo): unchanged. Scope declines to decide and
  `rel is None → git_error` still fires, preserving the CB-88 "git missing →
  `unreachable_commit`" pin.
- **OUT OF SCOPE:** CB-91 (cross-repo `file` values — ratified deferral), CB-95 (a
  location anchor more stable than a path + free-text `meta.lines`; filed from the user's
  request during this iteration's checkpoint), CB-51 (CSV import restore path — the only
  `high`, deferred by user decision to the next iteration with a design round).
- **`..` from a subdirectory now leaves the repository, and the pin cited above cannot see
  it.** `test_a_parent_traversal_landing_on_the_worktree_root_is_not_deleted` uses
  `file="src/.."` from the repo ROOT, where re-anchoring is a no-op. From a subdirectory
  every `..` climbs one level higher than before, so `'..'` and `'../top.py'` go from
  `modified`/`current` to `unknown`/`out_of_repo`. Both degrade honestly and both are
  contract-correct under ratified option (a). Measured: **0 of 3308 stored `file` values
  contain `..` or `./`**, 8 are absolute. Recorded because the risk section's coverage
  claim was wrong, not the outcome.

## Adversarial review — round 1

`adversarial-review-x2` was attempted. **Only ONE attacker actually reported: the Opus
adversary. Codex/Sol failed twice on process mechanics** (a `codex exec` that hung on
stdin, then a detached background task the relay could not retrieve). Stated rather than
papered over: this plan has had a single-model attack, not a cross-model one. Codex will be
re-run against the finished diff.

**Verdict: FAIL** — 1 FATAL, 1 SERIOUS, 5 MINOR. All addressed:

1. **FATAL — `-z` + `text=True` → `UnicodeDecodeError`.** Independently reproduced here:
   current C-quoting keeps output pure ASCII, `-z` emits raw bytes, and strict decoding
   raises a `ValueError` that the `(SubprocessError, OSError)` tuple does not catch. The
   probe carries no pathspec, so one undecodable rename in the range kills a whole batch.
   **The fix reintroducing this module's signature failure inside its own fix.** Fixed with
   `errors="surrogateescape"`; pinned by `TestUndecodablePathsDoNotKillTheBatch` (2 tests)
   and `test_premise_z_emits_raw_bytes_that_break_strict_decoding`.
2. **SERIOUS — the independence claim was false.** Withdrawn above, with the measurements.
3. **MINOR — `..` from a subdirectory.** Recorded above with the 0/3308 measurement.
4. **MINOR — the newline route was claimed fixed and never pinned.** Added
   `TestNewlineInAPathIsParsed`.
5. **MINOR — no premise test for the `-z` record shape.** Added `TestPremiseGitRecordShapes`
   (3 tests), in this repo's established `test_premise_*` shape.
6. **MINOR — no CHANGELOG entry for a declared contract change.** Added, under both
   `Changed` and `Fixed`.
7. **MINOR — `candidate` consumed outside the block binding it.** Bound beside `rel`, with
   both checked in the same guard.

**Checked and cleared by the review, worth not re-deriving:** `diff.relative=true` (which
`-z` does *not* suppress — `cwd=root` neutralises it), linked git worktrees, a symlinked
`project_dir`, all four scope pins (absolute-inside, absolute-outside, relative-into-another-repo,
in-repo symlink escaping), `rel == "."`, glob/prose/magic/empty/NUL values, and the
`_repo_root_hint` / `_ROOT_UNRESOLVED` batch path.

- **Sibling sweep (method + result).** Swept every `text=True` subprocess reader reachable
  from this module for the same decoding hazard, by running each against a repo holding a
  non-UTF-8 path and a non-UTF-8 commit subject. `git log --oneline`, `_parse_trailers`'
  `git log --pretty`, and `db.git_rev_parse` measured SAFE on this git version;
  `_kind_at_commit`'s `ls-tree -z` **crashes** and is fixed here as edit 3 — it is
  unreachable today only because its pathspec derives from a stored `str`, which is safety
  by argument rather than by construction. Not widened past this module.
