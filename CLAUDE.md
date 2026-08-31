# Codebugs

AI-native code finding & requirements tracker. SQLite-backed, exposed via MCP server + CLI.

## Workflow — `main` is never edited directly

**Every code edit happens on a short-lived branch, in a worktree, and `tools/` enforces it.**
Borrowed from `../autosorter` (2026-08-16), including a scaled-down port of its
`tools/worktree-*.sh` harness.

**What is now mechanically enforced** (`tools/install-hooks.sh` arms it; run once per clone):

| Rule | Mechanism | Refuses with |
|---|---|---|
| Branch carries `fix/`\|`feature/`\|`refactor/`\|`docs/` | `_guard_branch_type` (7) + pre-commit hook (1) | exit 7 / 1 |
| Nothing but `.claude/plans/*.md` or `.claude/plans/briefs/*.html` is committed on main | pre-commit hook | exit 1 |
| A plan note committed on main is NAMED in the commit message | commit-msg hook | exit 1 |
| A cascade id added to `.claude/plans/CASCADE-IDS.md` on main is the one `tools/cascade-mint.sh` would have computed (`max+1` per family, annulled lines and mentions included) | pre-commit hook | exit 1 |
| A merge onto main comes from a typed local branch, or from main's own upstream `main` | pre-merge-commit hook (clean merge) + pre-commit hook (conflicted merge) | exit 1 |
| An in-progress cherry-pick/revert marker no longer exempts a commit | pre-commit hook | exit 1 |
| Integration never fast-forwards | `--no-ff` + `git config merge.ff false` | — |
| One integration at a time | `flock` on `.worktrees/.integrate.lock` | exit 1 |
| The tested state still matched at the moment it was re-checked | in-lock SHA re-check | exit 13 |
| The suite ran under the interpreter main has | `_guard_interpreter_matches_main` | exit 14 |
| This clone is actually armed | `_guard_enforcement_armed` | exit 12 |
| Main has main checked out, and is clean | `_guard_workspace_on_main`, `_guard_main_clean` | exit 8, 11 |
| The branch actually carries a change | `_guard_nonempty_diff` | exit 9 |
| No conflict markers, no scratch or temp file at root, no stale base | `_guard_conflict_markers`, `_guard_untracked_scratch_at_root`, `_guard_stale_base` | exit 5, 4, 6 |

**`.github/workflows/main-invariants.yml` is deliberately NOT in that table**, and the reason is the
table's own title. It asserts that main's first-parent line carries nothing but merges and plan notes
— but a workflow **cannot refuse a push**, it reports afterwards, so listing it under *"what is now
mechanically enforced"* with a *"refuses with"* column was a category error inside the very table
meant to be precise (round-3 review). It is an **alarm**. The gate is branch protection on
`origin/main`; see the CI limits below.

**The re-check row was NARROWED, and what closes the remaining gap is a second alarm — not a gate
(CB-121).** That row used to read *"The tested state is the landed state"*, and it overclaimed: the
in-lock re-check is a **check-then-act**. The narrowed row is
still a checkable claim — the state matched under the lock, and a mismatch there really does refuse
with exit 13 before anything lands. It simply no longer promises the interval it cannot cover.

The gap is covered by a **post-merge alarm**, an alarm for the same reason `main-invariants.yml` is:
by the time it can look, **the merge step has already run**, so it cannot refuse anything and gets no
row in the table above. Immediately after the integration merge and **before** `flock -u 9` — after
the unlock another finish could move main, and the alarm would start lying in the way it exists to
catch — `worktree-finish.sh` asks whether the merge that just ran has `TESTED_MAIN` as its first
parent and `TESTED_HEAD` as its second. It then lets the cleanup
finish (worktree removal, claim release) and speaks at the very end, with a loud block and `exit 15`
— deliberately not `exit 13`, which means *nothing landed, re-run*. `exit 15` means *the merge step
already ran and the premise is unconfirmed*, and the block says in words not to re-run: a second
finish after a landed merge is a worse outcome than the defect being reported.

`merge.ff=false` is the one no hook could replace: **git fires no hook on a fast-forward at all**,
because no commit is created, so nothing can catch it after the fact. **Two precise limits:** it
does nothing when the branch is already an ancestor of main (git says "Already up to date" and main
does not move, which is harmless), and it is *configuration*, so `git config merge.ff true` turns it
off without anyone typing `--ff`.

→ почему именно так: `docs/claude-md-rationale/workflow.md#cb-121-четыре-несущие-детали`

- The trusted ref is matched **exactly** (`refs/remotes/<branch.main.remote|origin>/main`).
**A merge head with NO ref at all is refused, and that is a real cost, not a free win.** It catches a
bare SHA or a tag, but it also refuses four legitimate-if-rare flows, verified: a one-shot
`git pull --no-rebase <URL> main`, `git merge FETCH_HEAD` with no tracking ref, a `branch.main.remote`
set to a URL rather than a remote name, and `git merge <tag>` where no branch points at the tagged
commit. Ordinary `git pull` against a configured remote is unaffected, which is why this is
documented rather than fixed: closing it means trusting `.git/FETCH_HEAD`'s description text, and a
new trust path with no review round left to attack it is a worse trade than a rare `--no-verify`.
**Use `git merge --no-verify` for a one-shot pull from a URL.**

**And one limit that cannot be closed here, stated rather than papered over.** *Main's own upstream*
`main` is **trusted** — `branch.main.remote`, defaulting to `origin` — and nothing local can prove
what it contains or how it got there: `git update-ref refs/remotes/origin/main <any-sha>`, a mistyped
fetch refspec, a rewritten `remote.origin.fetch` (which then re-arms on every ordinary `git fetch`),
or simply an upstream whose `main` holds untyped work all land content here.
→ почему именно так: `docs/claude-md-rationale/workflow.md#cb-57-гейт-мержа`

→ почему именно так: `docs/claude-md-rationale/workflow.md#сторожа-читают-fail-closed`

**A plan note landing on main must be NAMED in the commit message, and the mechanism is a
`commit-msg` hook.** The rule it mechanises is that parallel sessions add files to main **by name,
never by directory**: `.claude/plans/` is the one place they may all write, and `git add
.claude/plans/` sweeps an UNTRACKED note belonging to another direction into a commit describing
unrelated work — the bytes survive, the **provenance** does not. **The
match must be flanked by a boundary: the string edge, or an ASCII byte that cannot occur in the
name.** Every **non-ASCII** byte counts as part of a name, so an ambiguous neighbour refuses rather
than matches; **the stated cost** is that a filename hugged by typographic quotes or dashes
(`«plan.md»`) is not recognised and needs a space or an ASCII quote around it. **So a staged basename containing a space or ASCII punctuation outside
`[A-Za-z0-9._-]` is REFUSED outright** rather than judged by a rule that cannot see it. Non-ASCII names are untouched, because a non-ASCII byte
is a NAME byte. **Scope, and what it deliberately does not touch.** Only `main`, and only `.claude/plans/*.md` or
`.claude/plans/briefs/*.html` (the second widened by CB-266 to match `pre-commit-hook.sh`'s own
widening, on the same reasoning: `git add .claude/plans/` recursively sweeps `briefs/`, so once a
brief can land at all, this hook's reason for existing reaches it too). **Deletions are in scope**, because `git add <dir>` stages a removal
too and deleting a stranger's note damages the same provenance. **A merge is exempt, and the
discriminator differs from `pre-merge-commit`'s in a way that would invert the rule if assumed:** a
clean merge writes no `MERGE_HEAD` at `pre-merge-commit` time, but by `commit-msg` time git **has**
written it, for clean and conflicted merges alike (measured, and pinned, because if a future git
stops doing it every integration would be refused). **What the exemption costs,
said plainly:** a *deliberate* operator can put the repo into a merge state (`git merge --no-commit`,
or any conflicted merge), stage an unnamed note, and commit — the naming rule is skipped. A clone armed
before T-23 is refused at its next finish until `tools/install-hooks.sh` is re-run, which is correct:
it really is missing a third of its enforcement. **What stays open:** the gate is invisible to the CI
alarm, which reads paths and not messages, and to `--amend` — an amend that changes only the message
stages nothing against HEAD, so a note already landed under a naming message can have that message
rewritten. Both are authored acts rather than accidents, which is what this hook is for.

→ почему именно так: `docs/claude-md-rationale/workflow.md#t-23-именование-заметки-плана`

**What this does NOT do, stated plainly because the honest scope is the point.** The local half is
CLIENT-SIDE and PER-CLONE: hooks and git config cannot be committed. A fresh clone has none of it
until `tools/install-hooks.sh` is run — which is why `_guard_enforcement_armed` refuses to integrate
from an unarmed clone, the one moment being unarmed can cost anything. **It checks all three hooks** —
pre-commit unconditionally, pre-merge-commit and commit-msg once their source is KNOWN (it has
history, or the file is present, or the history probe itself failed — fail closed) — so a clone armed
before CB-57 or before T-23 is refused until `install-hooks.sh` is re-run.

**Even armed, all of these move or publish `main` without passing any hook:** `git rebase`, `git am`,
`git reset --hard`, `git push`, `core.hooksPath`, **`git subtree add`** (which commits via
`commit-tree` plumbing), and **a CLEAN `git cherry-pick` or `git revert`**, where git's sequencer
commits directly. **Note the case split on those last two:** *clean* skips the hook entirely, while
the *conflicted* form is finished with `git commit` and **is** gated. **That case split covers
`commit-msg` too** — measured on git 2.53, a clean cherry-pick or revert reaches **neither** hook, so
it lands an unnamed plan note at exit 0; only the conflicted form is gated.
**A
typed branch committed in the *primary* checkout** also satisfies `pre-commit` while ignoring the
worktree rule entirely. **Most of these are what the CI job is for** — they flatten a non-merge commit
onto main's first-parent line, which is what `.github/workflows/main-invariants.yml` asserts against.

→ почему именно так: `docs/claude-md-rationale/workflow.md#общий-предикат-и-честная-область`

**The CI job's own limits, because a gate described better than it behaves is the failure this
section exists to record.**

1. It is scoped to a **pinned baseline SHA**, since main's history predates the rule. 

2. **Anything merge-shaped is invisible to it**, and `amend`/`rebase`/`reset` do **not** necessarily
   leave a non-merge commit on the first-parent line: `git commit --amend` on a *merge* stays a
   merge, `git rebase --rebase-merges` recreates merges, and a force-push to a fabricated merge-only
   history passes — `--no-merges` excludes all of it by construction. 

3. It uses **`--no-renames`**, and that is not cosmetic: with rename detection on, `--name-only`
   prints only the *destination* path, so `git mv src/keep.py .claude/plans/keep.md` shows one
   allowlisted path and deletes source from main. 

4. **A workflow cannot refuse a push by itself** — it reports afterwards. So this job is an
   **alarm**; the **gate** is branch protection on `origin/main`, ON since 2026-08-21 at a
   deliberately narrower scope than this list originally demanded. **Enabled: force pushes refused,
   branch deletion refused. NOT enabled: require-pull-request**, and with it the two settings that
   only mean anything behind a PR — marking `ci.yml`'s `tests` job required, and disabling squash-
   and rebase-merging.    **One residual open, one closed, both measured rather than assumed.** Still open: an **unarmed**
   clone can push a non-merge commit straight to `main`, since require-PR is off —
   `main-invariants.yml` is the alarm for that, not a gate. Closed on 2026-08-22:
   **`enforce_admins` is now `true`**, so the protection binds the owner too, where it had been
   advisory against his own credentials. **The cost of that switch is accepted and named:** an
   emergency rewrite of `origin/main`'s history now requires first turning `enforce_admins` off, an
   explicit repository-settings act rather than a `git push --force` typed in a hurry — which is
   exactly the friction the setting buys. **All of this is repository configuration, not committed
   state, so nothing in this tree can verify or restore it, and a later measurement — not this
   paragraph — is the authority.**

5. **`main-invariants.yml` deliberately does not subscribe to `pull_request`.** A job skipped by an
   `if:` is reported as **passing** for required-status-check purposes, so marking it required would
   have produced a check that can never fail on the only path where protection evaluates it — this
   section's own "gate that cannot fire", reintroduced inside its own fix. Lint and tests therefore
   live in a separate `ci.yml` which does run on PRs. 

6. It needs **`fetch-depth: 0`** because the AUDIT step reads history: with a shallow checkout the
   baseline commit is absent and `origin/main` may not exist, dropping the audit back to `HEAD`.
   **`ci.yml` needs the same key for a DIFFERENT reason, and that asymmetry is why it was missed for
   months (CB-139): there the history is read by the SUITE itself.** Exactly one test in the suite
   reads this repository's real history — `test_ci_workflow_asserts_the_first_parent_invariant` —
   so under the default depth-1 checkout `ci.yml`'s `tests` job was red in CI **always** and green
   in every local run, and a gate that cannot pass hides the regressions it exists to catch.
`contracts` stays
   shallow deliberately: it runs `tests/test_cli_signals.py` and `tests/test_fsio.py`, neither of
   which reads history.

→ почему именно так, с замерами и раундами ревю:
`docs/claude-md-rationale/workflow.md#пределы-ci-задачи`

**`.python-version` is the SINGLE SOURCE for the interpreter — of main, of every worktree and of
CI — and `_guard_interpreter_matches_main` refuses to land work the two of them did not agree on
(CB-135).** "Single source" is a claim about what DECIDED, not merely about what is written down,
and it needs that reading because **`UV_PYTHON` and `--python` outrank the file** (measured). So the
guard does not stop at "the pin exists": **it requires the interpreter uv actually chose to be the
one the pin asked for, and refuses when something outranked it.** Without that clause an exported
`UV_PYTHON` makes BOTH trees answer with the override, they agree, the gate passes, and the branch
lands a different pin that main would adopt on its next `uv run` — CB-135 rebuilt out of the very
mechanism this section documents.

**The pin is `3.14.4`, full patch, and the FULL-PATCH half of that was chosen rather than
defaulted.** A bare `3.14` (i.e. `MAJOR.MINOR`) leaves a divergent state representable — uv resolves
it to whatever 3.14.x a machine happens to have, so two machines legitimately differ — and this
guard's whole subject is making that state unrepresentable. **The cost is the ordinary cost of a
pin:** it must be bumped by a deliberate, reviewable edit, and a machine without that exact build
downloads one. Note `uv python list` shows
only the newest patch per minor, so checking downloadability needs `--all-versions`.

**`uv` rebuilds a mismatched environment by itself**, so the pin does most of the work and the guard
is there for what it cannot reach. Two consequences worth knowing before touching this: `UV_PYTHON=`
OUTRANKS the pin file, and a subsequent plain `uv run` snaps the tree back to the pin (both
measured) — which is why the repair command below is written with `UV_PYTHON=`, and why its effect
only has to survive until the finish completes.

**Fail-closed.** No `.venv` in main, no `uv` on `PATH`, a non-zero rc, an empty answer, an
unparseable answer — every one refuses with **exit 14**.
**The worktree must carry its own `pyproject.toml`, and that is not tidiness.** `uv run` resolves a
project by walking UP, and every worktree lives INSIDE the repo at `.worktrees/<slug>`, so a worktree
missing that file resolves against MAIN's project and the guard would compare main against main — an
agreement that can only ever hold, which is a gate that cannot fire, in the change whose subject is
precisely that.

**Phase, and `--skip-checks`.** The call sits in `[5/7]` AFTER the forward-merge — a
`.python-version` arriving from main must be in the tree before it is judged — and BEFORE `[6/7]`, so
a refusal costs seconds instead of the suite run it is declaring meaningless. It is **outside** the
`--skip-checks` branch: that flag skips ruff and pytest, which are CHECKS, and this is what decides
whether running them would have meant anything.
**A shared `.venv` is refused, and the comparison is between DIRECTORIES.** Point main's `.venv` at a
worktree's and the two sides become one environment: it can only agree, and the worktree removal at
the end of the finish then leaves main's link dangling.
**Bumping the pin is a two-step procedure, and the guard makes the order mandatory.** A branch that
changes `.python-version` is refused until main is brought to the NEW interpreter first —
`(cd <repo_root> && UV_PYTHON=<new> uv sync --extra dev)`, then re-run the finish. **A bare `uv sync`
is not enough**: it re-reads main's OLD pin and puts the old interpreter straight back. The refusal
prints that command with both versions filled in, because a gate with no way out is a wall rather
than a diagnostic.

**What this does NOT do.** It is per-clone and client-side like the rest of the harness — it says
nothing about the interpreter any other machine or CI actually used, only that these two trees agree.
It compares a version STRING, so two builds of the same version with different compile-time options
read as identical. A `.python-version` naming a build this machine cannot obtain fails at `uv run`,
which the guard reports as undeterminable — correctly, but the message will be uv's rather than a
diagnosis of the pin. And the pin is required to be a plain `X`, `X.Y` or `X.Y.Z`: uv also accepts
implementation and platform requests (`pypy@3.11`, a full `cpython-…-linux-…` triple), and rather
than guess what one of those resolves to the guard refuses and says so.

→ почему именно так: `docs/claude-md-rationale/workflow.md#cb-135-закрепление-интерпретатора`

- **Create:** `tools/worktree-setup.sh <type>/<slug> [base]`, which validates the name, refuses a
  card already carried by another branch, **claims every card the branch names through the claims
  ledger**, creates `.worktrees/<type>-<slug>`, and primes the worktree's own dev environment.
The
  card still reads `in_progress` while the branch holds it, because the status flip arrives as the
  claim's projection (`EntityKind.busy_status`).
**Exit codes are handled as the API they are**: `3` (held by someone else)
  is FATAL and prints the incumbent's triple — this is the **setup gate**, the one tracker call in
  the harness allowed to abort; `4` (already resolved) warns and proceeds, because a follow-up branch
  on a closed card is legitimate; `5` (undetermined) is retried **once** with the identical call,
  which converges rather than double-claiming because the primitive is an idempotent upsert. `CODEBUGS_SETUP_NO_CLAIM=1` skips the tracker entirely and is the
  documented escape hatch past a `3`; **`--allow-duplicate` deliberately does not punch through it**,
  because it answers a different question (another *branch* carries the id) and, since this repo
  never deletes merged branches, it is needed for ordinary follow-up work — overloading it would make
  the claim gate routinely bypassed. The *branch-name* collision check remains, and it is still the
  half that works with no tracker at all, because it is pure git. **What this does NOT do, and the
  honest scope is the point: a branch abandoned AFTER a successful setup still leaves a live claim.**
  Steal and expiry stay deferred by design (Claims module, below); `codebugs who-holds` names the
  holder and repo, and any close releases it, but that is not the claim disappearing. One concern per
  branch; a card-driven branch carries its id (`fix/cb-48-tracker-root-init`). Work already started
  on main moves over with `git stash push <files>` → setup → `git stash pop` in the worktree; the
  stash is shared across worktrees because it lives in the common git dir.

- **Worktrees live in `.worktrees/`,** slug = branch with `/`→`-`, matching autosorter. Both that
  directory and the legacy `.claude/worktrees/` are gitignored; the legacy path still works and
  `worktree-finish.sh` resolves either, but new worktrees go in `.worktrees/`.

- **Then work there, entirely.** Check which checkout you are in before any `Edit`/`Write` to a
  source file. **A surgical `git checkout <branch> -- <files>` onto main is editing main directly**,
  wearing a hat. Conflicts get resolved *inside* the worktree, never by committing a resolution on
  main.

- **Tests and lint run in the worktree, and it needs its own environment.** `uv run --extra dev
  python -m pytest tests/ -q` — **`--extra dev` is not optional there.** `pytest` and `ruff` live in
  `project.optional-dependencies`, which `uv run` does not install by default, so a fresh worktree
  dies with `No module named pytest` while main — synced long ago — works without the flag; the
  documented commands under **Testing** below are written for main and are incomplete here. `uv run`
  does build the worktree's own editable install pointing at the worktree, so once the extra is
  there, the isolation is real. **Never validate a worktree's changes by running the suite from
  main**: `pythonpath = ["src"]` resolves against the checkout you run in, so that tests main's
  source and passes on a tree you did not touch. The mirror-image trap is at the MCP-registration
  rules — from a worktree, a bare `python` reaches `codebugs` through main's editable install, which
  is why `tests/dump_schema.py` must be run with `PYTHONPATH=src`.

- **Integrate with `tools/worktree-finish.sh <slug> ['commit msg'] [--merge-msg '…']`.** It commits
  any dirty state, runs the guards, forward-merges main *into the worktree* so conflicts surface in
  safe space, runs `ruff check` and the full suite there against the combined tree, then merges onto
  main with `--no-ff` under the lock and removes the worktree. The merge commit is what makes a
  card's whole iteration recoverable as one unit; a fast-forward scatters it. **Never delete the
  branch** — no merged branch has ever been deleted here, and that is the record; the script removes
  the worktree only.

- **The integration message follows `Merge <branch>: <what changed> (CB-NN)`, and when it is not
  given it is derived from `main..<branch> --first-parent --no-merges --reverse` — the FIRST commit
  on the branch's OWN line among the commits main does not have (CB-116).**
  **`--first-parent` is load-bearing, not decoration, and restricting the range alone is NOT
  enough:** a branch that merges a SIBLING branch absorbs its commits into the range, and if the
  sibling is older — the ordinary case — date order puts it first, so the derived subject names the
  sibling's card; on that shape a range-only fix is **worse** than the `git log -1` code it replaced.
  **A branch with no commit of its own carrying a subject is REFUSED rather than guessed** —
  reachable when the content arrived through a merge commit, since `_guard_nonempty_diff` has already
  proved the content is real — and the derivation therefore runs at the `TESTED_MAIN`/`TESTED_HEAD`
  sample rather than under the lock, so that refusal costs nothing instead of the whole gate run.
  **One limit stays open and is documented rather than guessed at:** `worktree-setup.sh <branch>
  [base]` can cut a branch from a NON-MAIN base, whose commits sit on this branch's own first-parent
  line, so the derivation names the base's first commit.   **Pass `--merge-msg` on a branch cut from a non-main base.**

- **Every re-run hint echoes back the `--merge-msg` the aborted run was given**, and that half is
  orthogonal to the derivation — it would be needed even if the derivation were perfect, because the
  exit-13 refusal fires precisely BECAUSE main moved, so printing the bare short form routes the
  operator into the derivation that main's move had broken. **It echoes the `--merge-msg` and nothing
  else, deliberately:** `--skip-checks` and `--allow-stale-base` are relaxations, so dropping them
  makes a retry stricter, and the positional commit message applies only to a still-dirty worktree.

- **`ruff check` is the lint gate; `ruff format` is deliberately not**, because a large part of the
  existing tree is non-conformant to it and gating on it would refuse every finish. Pin ruff 0.15.7:
  0.16.x flags the whole repo.

- **Session end:** `git status` clean in main *and* in every worktree, then `git worktree remove
  <path>`. Never `--force`: a removal that refuses is telling you work is uncommitted there.

- **The only thing that may land on main directly** is a `.claude/plans/*.md` note — one level, not
  a subtree — or, since CB-266, a `.claude/plans/briefs/*.html` daily brief — one level under
  `briefs/`, not a subtree of it either, and no other extension. The pre-commit hook holds that
  line. **Name the note in the commit message, and add it to the index by name**:
  `git add -- .claude/plans/<note>.md`, never `git add .claude/plans/`.
  The commit-msg hook refuses a plan note the message does not name, which is the mechanised form of
  that rule. `git commit --no-verify` remains the escape hatch for both hooks: they exist to stop the
  accident, and an operator typing the flag has stated an intent.

→ почему именно так: `docs/claude-md-rationale/workflow.md#cb-58-и-cb-116-порядок-работы`

→ почему именно так: `docs/claude-md-rationale/workflow.md#как-проверяется-сам-харнес`

**The bootstrap is a real constraint, not an oversight.** `worktree-finish.sh` cannot land the commit
that first creates `tools/`, because `_guard_enforcement_armed` refuses when main has no
`tools/pre-commit-hook.sh` for the hook to point at. So **run
`tools/install-hooks.sh` right after such a merge** or the next finish refuses — correctly, since a
clone armed before the new hook really is missing part of its enforcement. If `tools/` is ever
rewritten the same way, expect the same one-time manual merge.

→ почему именно так: `docs/claude-md-rationale/workflow.md#исключения-маркеров-и-бутстрап`

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in `src/codebugs/__init__.py` —
   `tests/test_release_version.py` refuses a disagreement, the installed distribution included.

2. Retitle `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] — <date>`, leave an empty
   `## [Unreleased]` above it, and open the section with a highlights paragraph written for a user.

3. **After** the branch lands, tag the merge commit from the primary checkout
   (`git tag -a vX.Y.Z <merge-sha>`) — a tag made on the branch points at a commit that never landed.

## Architecture

- **Domain modules** (`src/codebugs/`): `db.py` (findings + shared infra), `reqs.py`, `bench.py`, `blockers.py`, `merge.py`, `sweep.py`, `embeddings.py` (vector storage/similarity search, delegates from reqs), `milestones.py` (releases / streams / capacity-aware pull)

- **Shared types** (`types.py`): Entity constants (statuses, priorities, severities), resolver functions, terminal states. Zero-dependency — safe to import from anywhere

- **MCP server** (`server.py`): Thin `MCPServer` orchestrator (~48 lines). Discovers tool providers via registry, filters by `--mode` flag. Requires the mcp 2.x SDK (`mcp.server.mcpserver.MCPServer`, which replaced 1.x's `mcp.server.fastmcp.FastMCP`)

- **CLI** (`cli.py`): Thin argparse orchestrator. Discovers CLI providers via registry, filters by `--mode` flag. Two entry points, and the split is load-bearing: `main()` is the importable body (three test modules call it in-process), while `run()` — what `[project.scripts]` and `python -m codebugs.cli` reach — first restores the POSIX `SIGPIPE` disposition (CB-78) and then refuses to run at all when stdout is already closed (CB-134). 

- **Formatting** (`fmt.py`): Shared CLI output utilities (ASCII table formatting). Text for a stream, nothing else — file writing deliberately does NOT live here (CB-76)

- **Filesystem output** (`fsio.py`): `atomic_write` — the only sanctioned way a CLI handler writes a file. Owns tempfile lifecycle, destination classification and atomic replacement; imports nothing from the package. See the export rule under **CLI** below

- **Storage**: Single SQLite DB at `.codebugs/findings.db`; each domain module owns its schema via `ensure_schema(conn)`

## Code rules

### Naming and style

- Python 3.11+. Type hints on all public function signatures.

- `ruff` for linting/formatting, line length 100.

### Database

- Use parameterized queries exclusively. Never interpolate values into SQL.

### Testing

- Run tests: `uv run python -m pytest tests/ -v`

- Run lint: `uv run ruff check src/ tests/`

- Run format: `uv run ruff format src/ tests/`

### CLI

**`cli.run` REFUSES at the process entry, before any work, with the same 141** — one vocabulary for one condition ("the reader of my output is gone"), uniform on 3.11 through 3.14, measured by the `contracts` matrix in `.github/workflows/ci.yml` (`test_cli_signals.py` + `test_fsio.py`). **Honest scope: 3.15 and later are admitted by `requires-python` and are NOT verified** until they are added to that matrix; narrowing the sentence to the pinned version alone was rejected as the more expensive option, since it would leave `requires-python = ">=3.11"` advertising a range nothing checks. **The price is a real behaviour change on 3.13 and is named rather than absorbed**: a closed-object stdout there used to let the write land and then fail on output, and now lands nothing — which is the point, since with the refusal ahead of the work there is no committed write left to misreport.
**Four residuals, each measured, none a regression** (every one proceeds today too): `fileno()` does not govern `write()` — `io.TextIOBase()` raises `UnsupportedOperation` from both, so it is accepted and then fails, and refusing it instead would refuse every pytest capture object; a writable descriptor can still fail to be written (`/dev/full`, a full filesystem, a hung-up PTY), which is a **write failure, not a closed stdout**, and needs its own outcome as a separate negotiation; a file opened for WRITING landing on fd 1 passes the probe and takes the output; and **the 141 is not unconditional** — finalization also flushes `sys.stderr`, and a failing stderr flush rewrites the status to 120 even with `sys.stdout = None`, reachable only by installing an stderr in-process before `run`, so no CLI invocation reaches it and making it unconditional would mean `os._exit`.

## Architecture migration (in progress)

We are migrating toward a plugin architecture in phases.
**Current rules for new code:**

- New domain modules must call `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` at module level — do NOT edit `db.connect()`, `server.py`, or `cli.py`.

## Embeddings

`embeddings.py` stores a vector per requirement and answers similarity queries over them.
**There is no embedding provider in this package, and that is the fact every other rule here follows
from.** The CALLER computes the vector, in its own process, and passes finished numbers as
`embedding: list[float]`; the tools never receive the requirement's TEXT at all.

## Claims module

`claims.py` answers "who currently holds this entity" for findings and requirements, so parallel
agents can refuse to duplicate each other's work. 

- **Outcomes, not booleans**: `claim` → `claimed | already_mine | held_by_other | entity_terminal |
  undetermined`; `release` → `released | not_yours | not_claimed | undetermined`.   `undetermined` means the database was too contended to tell — **re-issue the identical call**; the
  primitive is an idempotent upsert, so a replay converges on `already_mine` and can never
  double-claim.

- **Ownership is the triple** `(holder, holder_kind, holder_repo)`, compared NULL-safely. Both claim
  and release authorize on the full triple: a same-text holder of another kind or in another repo is
  a different claimant.

- **Exit codes are the API for shell callers**: `0` proceed, `1` error, `3` held by someone else,
  `4` already resolved, `5` contended (retry). `codebugs claims --format ids` prints bare ids and
  exits 0 on an empty list so a shell loop needs no parsing.
  **`141` was added package-wide by CB-78** and is not a claims outcome — it is documented here only
  because this is where the exit-code list lives; the **CLI** section owns it. It is `128 + SIGPIPE`,
  meaning *the reader of my stdout **or stderr** went away* (the disposition is process-wide, so
  `codebugs bad-verb 2>&1 | head -0` yields it too), and it can come back from any verb. A `| while read` loop that `break`s
  kills the producer at 141 rather than 1; both are non-zero, so no `set -e` script changes
  behaviour. **Observable only when the reader closes without draining (any size) or un-drained
  output exceeds the 64 KB pipe buffer.**
  **`74` was added by CB-136** and is not a claims outcome either — same reason it is recorded here,
  same **CLI** ownership. It is `EX_IOERR` from `sysexits(3)`, meaning *my output could not be
  WRITTEN* on a descriptor that was healthy at the process entry — `/dev/full`, a filesystem that
  filled while the verb ran, a wedged PTY — and it **deliberately asserts nothing about whether the
  command's effect landed**, because the write that failed is usually the line reporting a mutation
  that has already committed. **`141` is deliberately not reused**: there the reader is gone, here it
  is present and the medium is full, and blurring that is what CB-78 refused. When a verb had already
  chosen its own non-zero code, `74` wins, since the caller never received the output that code
  describes. **Three limits, each measured rather than assumed.** *`EPIPE` is excluded and reports
  `141`*: `cli.run` restores the SIGPIPE *disposition* but cannot clear an inherited signal *mask*,
  so a caller that blocked SIGPIPE gets `EPIPE` back from the write instead of dying by signal, and
  calling that "the medium is full" would undo CB-78 inside CB-136's own fix. *It covers what goes
  through `sys.stdout`* — `print` and the `csv` writer, i.e. every verb's ordinary output — and NOT
  `export-csv <path>`, where `fsio.atomic_write` writes through its own file object and CB-76's arm
  still reports exit 1, `export-csv /dev/stdout` included; that is unchanged behaviour rather than a
  hole this opened, and nothing is committed on that path, so it is not the CB-15/CB-16 lie. *A verb
  that CRASHES* keeps its traceback and its own code, so a still-buffered stdout can reach `120`
  there as before — trading a crash's traceback for a tidy code is the worse of the two.

- **Adoption**: autosorter's `worktree-setup.sh` claims every card in the branch name (and in
  `--items`) **before** `git worktree add`, with an EXIT trap that releases them if setup aborts;
  `worktree-finish.sh` releases whatever the branch still holds. **Exactly one of those calls may be
  fatal — the setup gate.** Everything else is guarded, so a missing or contended tracker can never
  abort a finish after the merge has landed. This repo's own `tools/worktree-*.sh` follow the same
  shape (CB-58).   **Two places codebugs deliberately diverges from `FINAL-DESIGN.md` §6.2–§6.3, both because that
  section was written for autosorter's script and one of its premises does not hold here.** Do not
  "fix" either back without reading this.
  1. **`--allow-duplicate` does NOT clear a `held_by_other` refusal** (design §6.2(a) has it clear
     both `3` and `4`). That flag also clears the pure-git branch guard, and this repo never deletes
     merged branches, so it is needed for *ordinary follow-up work* — one flag for both jobs would
     turn the claim gate off exactly when people are doing normal work. `CODEBUGS_SETUP_NO_CLAIM=1`
     is the typed alternative and it builds with **no** claim rather than stealing one.   2. **Finish leaves restore ON** (design §6.3 passes `--no-restore`). **This repo has no auto-resolve step**, so
     the card is typically still `in_progress`, and `--no-restore` would leave every finished
     branch's card `in_progress` with no holder: CB-58's own defect, reintroduced by CB-58's fix.
     Restore is a CAS against the projected value, so it still cannot resurrect a card someone
     already closed; the operator-closed case returns `not_claimed` at exit 0 and writes nothing.

## Milestones module

Releases ("release/1.1") and standing streams ("stream/triage", "stream/maintenance", "stream/security") give parallel-agent work a durable bucket. 
