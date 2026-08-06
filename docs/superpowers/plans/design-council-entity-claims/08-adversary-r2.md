# Adversary — Round 2

**Target:** `07-architect-r2.md` (1761 lines), the converged Claim Ledger design.
**Posture:** Round 1's verdict is not relitigated. The user chose to build B1; that decision stands.
This round asks only: *were the defects fixed, are the citations real, and is what is now on the
table still the thing that was approved?*

Every finding below carries evidence I opened or executed **this run**. Probe scripts are in
`…/scratchpad/probe1.py` … `probe4.py`.

---

## 0. What I executed this run

Environment: **Python 3.13.3, SQLite 3.47.1** (same build the architect reports).

| Probe | Result |
|---|---|
| `sqlite3.SQLITE_BUSY/LOCKED/ERROR/BUSY_SNAPSHOT` | `5 / 6 / 1 / 517` — architect's P1 **confirmed** |
| Nested `BEGIN IMMEDIATE` | `OperationalError: cannot start a transaction within a transaction`, `sqlite_errorcode=1`, `SQLITE_ERROR` — **confirmed** |
| `ROLLBACK` with no txn | `cannot rollback - no transaction is active`, code `1` — **confirmed** |
| Contention, `busy_timeout=0` | `database is locked`, code `5`, `SQLITE_BUSY` — **confirmed** |
| §6.1 upsert, verbatim | fresh → `(c1, touch=1, was_new=1)`; same holder → `(c1, touch=2, was_new=0)`; other holder → **`None`, rowcount 0** — **confirmed** |
| §6.2 soft release then re-claim by another holder | release returns row; re-claim inserts **new** `claim_id`; both rows coexist, one live — **confirmed** |
| §6.3 steal stmt 1 with wrong `expected_holder` | `fetchall() == []`, no write — **confirmed** |
| §6.4 index use, 200 000 rows / 200 holders | `who_holds` 0.05 ms `SEARCH … idx_claims_live`; `held_by` 0.19 ms `SEARCH … idx_claims_holder_live` — **confirmed** |
| §6.4 `list_claims` SQL as printed | **`sqlite3.ProgrammingError: Incorrect number of bindings supplied`** — see W-1 |
| `db.git_rev_parse` with the §1/L3 predicate string | returns the literal `'--verify refs/heads/live-branch'` — see W-3 |
| `db.git_rev_parse` failure modes | missing branch → `None`; `cwd=/nonexistent` → `None`; `cwd=/tmp` non-repo → `None`; `silent=False` on a missing ref → **raises `CalledProcessError`** |
| `worktree-setup.sh` census across `/home/faxik/w` | 16 copies at **274** lines, 13 at 171, 17 at 172, plus 151/131/118/98. **Zero copies at 275 lines.** All 274-line copies: `worktree add` at **:143**, `for cb in ${_claim_ids}` at **:208**, "~41 cards" at **:120** |

---

## 1. Were the eight fixes fixed?

First, a framing note the caller should have. **The `FATAL-1 … MEDIUM-8` numbering is the
architect's own, invented in §17.** Round 1 used `X-1 … X-5` for cross-cutting findings plus
per-proposal FATAL/SERIOUS items; there is no `MEDIUM` tier anywhere in `05-adversary-r1.md`,
`06-judge-r1.md`, or `CHECKPOINT-r1.md`. The defendant wrote the indictment. I therefore checked
both directions: are the eight fixed, **and** did the eight silently drop something Round 1 raised.
They did — see **S-5**.

### FATAL-1 — nested transaction in `pull_next` → **FIXED**

The core/wrapper split is real, not decorative. `capacity.py` calls `claims._claim_core`
(`07:1138`), the core layer's contract is "emits statements only, NEVER commits" (`07:466`), and
enforcement is a test that opens a transaction, calls every `_*_core`, and asserts
`conn.in_transaction` is still `True` (`07:485-487`, test 11). Nesting is not avoided at runtime —
it is never attempted. That is the structurally correct answer, and it is the one the Round-1
adversary itself proposed.

`db.txn`'s reentrancy is correct **for the case it was written for**: I confirmed
`conn.in_transaction` is `True` after an explicit `BEGIN IMMEDIATE`, so an ambient transaction is
detected and the inner frame does nothing. The `if conn.in_transaction`-guarded `ROLLBACK` with a
swallowed `OperationalError` (`07:430-434`) genuinely prevents cleanup from replacing the real
exception. `except BaseException` (`07:429`) is the right choice.

It is not correct for a case the design itself documented and then did not connect — see **S-3**.

`release_item`'s silent half (`07:1177-1191`) is a real find the architect made on its own:
`release_item` commits at `capacity.py:274` after three unatomic writes, and a partial commit there
would be silent rather than loud. Fixing it is correct and is honestly labelled "an atomicity bug
fix independent of claims".

### FATAL-2 — `rowcount` on `RETURNING` → **FIXED**

I audited the 8-statement table at `07:772-782` against the actual statements printed elsewhere in
the document, not against its own summary:

| # | Design's claim | Verified against |
|---|---|---|
| 1 | claim upsert, `fetchone()`, computed `was_new` | `07:611-623` — no `rowcount` read; my probe reproduces the three-way outcome |
| 2 | held_by_other lookup, no `RETURNING` | `07:640-643` ✅ |
| 3 | `UPDATE … SET prev_status` , outcome not consulted | `07:650-653` ✅ |
| 4 | `set_status`, **no** `RETURNING`, `rowcount` | `07:883-887` ✅ — and the docstring at `07:879-880` states the reason |
| 5 | release soft-delete, `fetchone()` | `07:667-674` ✅ |
| 6 | steal stmt 1, `fetchone()` | `07:698-707`, with the comment `# <-- FATAL-2: FETCH. Never cur.rowcount.` ✅ |
| 7 | steal stmt 2, no `RETURNING` | `07:714-719` ✅ |
| 8 | audit prune, `fetchall()` | `07:1089-1096` ✅ |

The audit is honest and complete. The CLAUDE.md rule it derives (`07:783-785`, "a statement either
carries `RETURNING` and its outcome is read by fetching, or it carries no `RETURNING` and its
outcome is read from `rowcount`; never both") is the right generalisation.

**Bonus, and it is a real one:** §6.3 found a second `steal` defect nobody raised in Round 1 —
`prev_status` must be carried forward from the victim row or the thief's release restores to
`in_progress` instead of the pre-claim `open`, permanently pinning the finding (`07:722-726`).
That is a genuine independent discovery.

### SERIOUS-3 — `undetermined` masked by its own handler → **FIXED at the root**

Executed on this build, not taken on trust:

```
NESTED BEGIN     -> 'cannot start a transaction within a transaction'  code 1  SQLITE_ERROR
ROLLBACK-no-txn  -> 'cannot rollback - no transaction is active'        code 1  SQLITE_ERROR
CONTENTION       -> 'database is locked'                                code 5  SQLITE_BUSY
SQLITE_BUSY 5  SQLITE_LOCKED 6  SQLITE_ERROR 1  SQLITE_BUSY_SNAPSHOT 517
```

The design's classifier `(code & 0xFF) in {SQLITE_BUSY, SQLITE_LOCKED}` (`07:565-571`) correctly
catches `517 & 0xFF == 5` and correctly re-raises code `1`. String matching is gone. Two mechanisms
now stand between the caller and the Round-1 failure — the guarded `ROLLBACK` (so the second
exception is never raised at all) and the code-based classifier (so if it were, it would propagate
rather than be laundered). Tests 14 and 16 cover both directions. This is the cleanest fix in the
document.

Note for the implementer: the repo currently has **zero** occurrences of `sqlite_errorcode`,
`OperationalError`, or any retry logic anywhere in `src/` — this is entirely new discipline, not
an extension of existing practice.

### SERIOUS-4 — new `db.py` infrastructure with one consumer → **JUSTIFIED, honestly**

The five arguments at `07:1002-1020` are fair. The strongest is #2 and it checks out: `db.py`
already owns `register_post_add_hook` (`db.py:178-204`) with an identical contract — runs in the
caller's transaction, before commit, exceptions swallowed to stderr — and an identical consumer
shape. The update side genuinely does not exist. Argument #1 (it is the only mechanism reaching the
live call site) is now **weakened by the design's own §13.2(d)**, which *deletes* that call site:
once `codebugs update --status in_progress` is removed from `worktree-setup.sh`, the hook no longer
sits on live traffic from that script. The design does not notice the tension. Not a defect —
`provenance.py` still routes through `update_finding` — but argument #1 should not be leaned on.

The disclosed fallback (`07:1022-1029`: if the seam is rejected, degrade to read-time `divergent`
reporting) is a real fallback, not a rhetorical one, because `divergent` is computed on every read
(`07:762-763`) independently of the hook.

### SERIOUS-5 — terminal hook findings-only → **FIXED**

Fired from both `findings.update_finding` (between `findings.py:298` and `:299`) and
`reqs.update_requirement` (between `reqs.py:222` and `:223`) — `07:948-954`. My delegate confirmed
both functions do their own `conn.commit()` at those exact lines, so both needed it and both get it.
Test 17 covers the requirement side specifically. Correct.

### SERIOUS-6 — B1→B3 not an additive upgrade → **RETRACTED and REDESIGNED, correctly**

`claim_id TEXT PRIMARY KEY` + `CREATE UNIQUE INDEX … WHERE released_at IS NULL` (`07:335-336`).
I executed the exact schema and the exact statements:

```
fresh       -> (c1, branch-a, touch=1, was_new=1)   rowcount 1
same holder -> (c1, branch-a, touch=2, was_new=0)   rowcount 1
other hold  -> None                                 rowcount 0
release     -> [{'claim_id':'c1', ...}]
reclaim b   -> (c4, branch-b, touch=1, was_new=1)
all rows    -> c1 released_at='T2' | c4 released_at=None
```

Exclusion is identical to a PK, and closed rows accumulate freely. The retraction at `07:1696-1697`
is explicit and correct. This is the single largest structural improvement over Round 1.

### SERIOUS-7 — an audit fact with nowhere to live → **FIXED**

Soft delete gives `release_reason='terminal:fixed'` a home, and
`SELECT release_reason, count(*) … GROUP BY 1` (`07:367`) becomes the health check for the clearers.
The reasoning at `07:361-369` — that history is not bought for its own sake but is what makes L4
auditable — is sound.

### MEDIUM-8 — "third kind by declaration only" → **FIXED**

`EntityKind.busy_status: str | None = None` (`07:850`). I read `entities.py:23-33`: `EntityKind` is
a frozen dataclass whose fields all lack defaults, and `ENTITY_KINDS` (`entities.py:36-55`)
constructs both kinds with **keyword arguments only**. A trailing defaulted field is therefore
strictly back-compatible, exactly as `07:866-868` claims. `claims.py` branches on nothing; it reads
`ref.kind.busy_status`. Criterion 4 is met literally, and test 5 proves it with a synthetic third
kind. The projector registry is gone. Correct.

**But the field placement introduces a new defect — see S-1.**

---

## 2. New problems

### S-1 [SERIOUS] `entity_terminal` is unreachable for requirements, and whenever `--no-project` is passed

§6.1's guard (`07:598-605`):

```python
busy = ref.kind.busy_status
if project and busy is not None:
    current = ref.status(conn)
    if current in ref.kind.terminal and not allow_terminal:
        return {"outcome": "entity_terminal", ...}
```

Requirements declare **no** `busy_status` (`07:856`: `EntityKind(name=t.ENTITY_REQUIREMENT, …)` with
the field omitted → `None`). So for every `FR-`/`NFR-` id the whole block is skipped and the terminal
check never runs. Consequences, all unstated:

- `codebugs claim FR-7` on a `verified`, `superseded` or `obsolete` requirement returns **`claimed`,
  exit 0**. §13.1's exit code 4 is dead for requirement ids.
- §11.1's advertised secondary bug fix — *"`entity_terminal` means `pull_next` now refuses to pull an
  item whose entity is already resolved. Today it will happily hand you a fixed bug"* (`07:1172-1173`)
  — is **false for `item_kind='requirement'`**. `pull_next` will still happily hand you a verified
  requirement.
- `claim CB-1 --no-project` on a `fixed` finding also succeeds, because the same `project` flag gates
  the guard.

Terminality is a property of the entity, not of whether the kind projects: `EntityKind.terminal` is
populated for **both** kinds (`entities.py:26-31`, `terminal=t.FINDING_TERMINAL` /
`terminal=t.REQUIREMENT_TERMINAL`). The guard was gated on the wrong condition. Fix is one
de-indent, but as written §7.2's "claiming a `fixed` finding is not a coordination event, it is a
mistake" only holds for half the entity space.

### S-2 [SERIOUS] `pull_next` gains a `KeyError` failure mode on orphaned `item_ref`s

`_claim_core` calls `EntityRef.of(entity_id).require(conn)` before any insert, which raises
**`KeyError`** for a well-formed id with no row (`07:598`, `07:1055`, §3.2 point 1). §11.1 inserts
that call inside `pull_next`'s candidate loop (`07:1138`). `pull_next`'s existing handler is
`except Exception: conn.execute("ROLLBACK"); raise` (`milestones/capacity.py:214-216`, read this
run). So a milestone item whose `item_ref` points at a deleted finding turns `pull_next` from
"returns an item" into "raises `KeyError`".

This is not hypothetical: the design's own §3.2 point 3 cites *"`milestones/triage.py` already
catches `KeyError` for a deleted finding rather than relying on cascade. This is the house style"*
(`07:389-390`) — which is direct evidence that orphaned `item_ref`s occur and are handled elsewhere.
`pull_next` is the highest-traffic MCP entry point in the milestones package and is called by
autosorter's `worktree-setup.sh` tooling. Not disclosed in §11.1, not in §14's back-compat list, not
in §18's risks.

### S-3 [SERIOUS] `db.txn`'s reentrancy assumes every ambient transaction has an owner. The design's own probe P8 proves that is false.

`db.txn` (`07:418-420`):

```python
if conn.in_transaction:
    yield False                     # ambient: the caller owns it
    return
```

The docstring justifies this as *"the owning frame keeps full control of the outcome"* (`07:411-412`).
That is true only when an owning frame exists. `db.connect()` (`db.py:492-503`, read this run) sets
`journal_mode=WAL` and **nothing else** — it never touches `isolation_level`, so every connection in
this codebase runs in legacy `isolation_level=''` mode. The architect's own probe **P8** records the
consequence (`07:33`): *"Legacy mode (`isolation_level=''`): plain DML then no commit — `in_transaction`
flips `False→True`."* Such a transaction has **no owning frame**. Any public `claim()` / `release()` /
`steal()` reached on a connection where an earlier statement opened an implicit transaction will
take the `yield False` branch, execute its writes, issue **no `COMMIT`**, and return `claimed` to the
caller. The write is then at the mercy of whatever the next `commit()`/`close()` does.

The design states the premise (P8) and states the mechanism (§4.1) on consecutive-ish pages and never
connects them. This matters more than a single call site because §14 amendment 3 makes `db.txn` the
**only** sanctioned transaction primitive in the repo — every future caller inherits the hole. The
correct contract is either `in_transaction and <an explicit ownership flag>`, or a documented
precondition that `db.txn` must never be entered from legacy-mode implicit-transaction state.

### S-4 [SERIOUS] `capacity.py → claims._claim_core` violates the CLAUDE.md rule the same section cites

CLAUDE.md, *Module structure*, verbatim: *"Domain modules may import `db` for connection/ID
utilities. They must NOT import each other's private functions — only public interfaces."*

§2 (`07:253-254`) specifies `milestones/capacity.py` calling `claims._claim_core()` and
`claims._release_core()`. §11.1 (`07:1138`) and §11.2 (`07:1188`) spell out the calls. §2.1
(`07:263-266`) then asserts of exactly this dependency: *"it uses the same public-interface-only
discipline CLAUDE.md mandates."* Those two statements cannot both be true.

The fix is trivial — promote the core layer to public names (`claim_in_txn`, `release_in_txn`) or
add the exemption to §14's CLAUDE.md amendment list, which currently has five items and does not
include this one. But the design as written both breaks the rule and claims to follow it, and §17
scores FATAL-1 "FIXED, structurally" on the strength of the mechanism that breaks it.

### S-5 [SERIOUS] The one finding *both* Round-1 adversaries called FATAL is absent from the §17 ledger

Codex/Sol's `major_risks` #1, against **all nine** proposals: *"the architects renegotiated a hard
requirement instead of satisfying it"* — `00-problem-brief.md:148` requires projection for findings
**and** requirements; B opts requirements out at `02-architect-b.md:61`. The Opus adversary raised
the same thing as **X-4 [SERIOUS — convergent rationalization]**: *"`busy_status=None` sells a scope
cut as a design win."*

R2 still opts requirements out (`07:856`), and §8.1 presents it as a *feature*: *"Criterion 5 is met
the same way: `requirements` declares no `busy_status`, so it gets full ownership … per the SETTLED
ruling"* (`07:864-866`). §17's eight-row ledger does not mention it. `CHECKPOINT-r1.md`'s recorded
user decision is *"Настоящее поле — строим B1"* and the orchestrator-verified facts note only
*"`reqs.py:22` — requirements CHECK excludes `in_progress`"*; neither says the brief's projection
requirement was waived.

I am not asserting the requirement *should* be met — reopening `reqs.py:22-23`'s CHECK may well be
the wrong trade, and S-1 shows requirements-without-projection has other consequences. I am asserting
that **the ledger the architect wrote to prove it addressed every Round-1 defect omits the defect
both adversaries scored highest**, and re-labels it "SETTLED" without a citable settlement. That is
exactly the move the design elsewhere condemns as "laundering".

---

## 3. The shell diff — the part the design says decides everything

`07:10-11`: *"a ~65-line diff across two shell scripts in a second repository (§13.2, §13.3) — which
is the part that decides whether any of the rest matters."* I attacked it hardest for that reason.

### F-1 [FATAL] §13.3's release block aborts `worktree-finish.sh` on its **normal** path

The proposed block (`07:1463-1472`):

```bash
if [[ "${SKIP_CHECKS}" != true ]] && command -v codebugs >/dev/null 2>&1; then
    codebugs claims --holder "${BRANCH_NAME}" --json 2>/dev/null \
      | grep -oE '"entity_id": *"[^"]+"' | sed -E 's/.*"([^"]+)"$/\1/' \
      | while read -r cb; do
            codebugs release … >/dev/null 2>&1 || true
        done
fi
```

`worktree-finish.sh:11` is `set -euo pipefail` (read this run). `grep` exits **1** when it matches
nothing. Under `pipefail` the pipeline's status becomes 1, the pipeline is a plain command in the
`then` body (not a condition), and `set -e` terminates the script — **mid-integration, after the
`merge --no-ff` at `:1198`**. The `|| true` is inside the `while` body and does nothing for the
pipeline's exit status.

The design itself establishes that no-match is the *expected* case: *"By the time this block runs it
is usually a no-op — and that is the point"* (`07:1477`). So the common path kills the script.

This is not an exotic bash trap; it is the trap the surrounding file already guards against. Both
existing external-tool invocations end in `|| echo "  ⚠ … failed (non-fatal) …"`
(`worktree-finish.sh:1123` and `:1136`). §13.3 explicitly claims to be written *"in the same guarded
style"* (`07:1461`) and is not. One trailing `|| true` on the pipeline fixes it — but as written,
shipping §13.3 breaks branch integration for everyone.

### F-2 [SERIOUS] The `ERR` trap does not cover the two failure modes it exists for

§13.2(c) (`07:1426-1437`) installs `trap '_release_claims_on_abort' ERR` **after** the claim loop,
and the design calls it *"not optional"* (`07:1441`) and *"would not cut, at any budget"* (`07:1748`).
Three holes, all verified against the real script:

1. **`exit` does not fire an `ERR` trap in bash.** §13.2(b)'s case arms 3 and 4 both end in
   `exit 1` (`07:1411`, `07:1413`). `CB_IDS` is a `sort -u` list and the guard loop is
   `for cb in ${CB_IDS}` (`worktree-setup.sh:75-82`), so a branch naming two cards can claim CB-A,
   then hit `held_by_other` on CB-B, then `exit 1` — leaking CB-A's claim. And the trap is not even
   installed yet at that point, because it goes *after* the loop.
2. **`SIGINT` does not fire an `ERR` trap.** Ctrl-C between the claim and `git worktree add` — a
   40-second window in a script that clones a venv — leaks the claim.
3. The script has `set -euo pipefail` but **not** `set -E` / `errtrace`, so the trap is not inherited
   by functions or subshells.

The correct primitive is `trap … EXIT INT TERM` with an idempotent handler and a success-path
disarm, not `ERR`. A leaked claim is, by the design's own §1-L5 argument, the exact pathology that
would turn `entity_claims` into the 42nd stale row — so the mechanism guarding the design's largest
stated risk does not work as specified.

### F-3 [SERIOUS] Every insertion anchor in the shell diff points at a file that does not exist

Adjudicated in full in §6 below. Summary: the design specifies its insertions at `:86`, `:105-133`,
`:110-111`, `:134`, `:141`, `:206-212`. The real file has them at `:90`, `:107-135`, `:112-113`,
`:136`, `:143`, `:208-214`. An implementer following §13.2 literally would splice a `case` statement
into the middle of the git guard's `if`.

---

## 4. The trustworthiness mechanism (§1, L1–L5)

### STRENGTH — L3's two-call verifier is the right design, and I confirmed the failure modes it guards

```python
if db.git_rev_parse("HEAD", silent=True, cwd=holder_repo) is None:
    return "unverifiable"
if db.git_rev_parse(f"refs/heads/{holder}", silent=True, cwd=holder_repo) is None:
    return "gone"
return "live"
```

`db.git_rev_parse` (`db.py:210-226`, read this run) is
`(ref: str, *, silent: bool = False, cwd: str | None = None) -> str | None`, wrapping
`subprocess.check_output(["git","rev-parse", ref], …, cwd=cwd)` and catching
`(SubprocessError, FileNotFoundError)`. Executed against a throwaway `git init` repo:

| Call | Result |
|---|---|
| existing branch, `silent=True` | SHA |
| missing branch, `silent=True` | `None` |
| `cwd='/nonexistent/path'` | `None` |
| `cwd='/tmp'` (not a repo) | `None` |
| `refs/heads/my branch`, `refs/heads/HEAD`, `refs/heads/` | `None` |
| missing branch, `silent=False` | **raises `CalledProcessError`** |

So `git_rev_parse` genuinely cannot distinguish "branch absent" from "git unavailable" — and the
HEAD probe genuinely separates them. Without it, `claims-audit --prune` would soft-close every live
claim on any machine where git is missing or `holder_repo` has been moved. **`07:1057-1061` is
correct and the design deserves credit for noticing it.** The argument-injection note (`07:1063-1065`)
is also correct: `holder` becomes an argv element, is always prefixed with `refs/heads/`, and cannot
be read as an option. Verified.

The `verifier` injection (`07:1067-1069`) is the right boundary — `claims.py` never shells out from
inside an MCP request, and the default `None` → `liveness="unverified"` → nothing pruned is fail-safe.

### W-3 [WEAKNESS] §1's headline predicate is not expressible through the helper §10.1 uses

§1/L3 states the predicate as *"`git -C <holder_repo> rev-parse --verify refs/heads/<holder>`
succeeds"* (`07:173-174`), and §13.2(b)'s user-facing hint prints exactly that command to the
operator (`07:1407`). `db.git_rev_parse` takes a single `ref` and builds `["git","rev-parse", ref]`
— there is no flag channel. Executed:

```
db.git_rev_parse("--verify refs/heads/live-branch", silent=True, cwd=repo)
  -> '--verify refs/heads/live-branch'      # truthy garbage, not a SHA
```

§10.1's implementation quietly drops `--verify` and is *behaviourally* fine (bare
`rev-parse refs/heads/GONE` exits 128 → `None`, verified). So this is a spec/implementation mismatch,
not a runtime bug. It matters only because §1 leans on the exact wording as "the core of the answer"
and an implementer copying §1 into `git_rev_parse` gets a verifier that reports every claim `live`.

### W-4 [WEAKNESS] `holder_repo` is caller-supplied and unvalidated, in a cross-repo tracker

codebugs is a cross-project tracker; `db.connect()` walks up from cwd and one DB serves whatever
repo it sits under. `holder_repo` is whatever the caller passed (`--repo "${REPO_ROOT}"`,
`07:1401`). Nothing checks that the repo has any relationship to the entity. Two repos can hold
branches with the same name; a claim whose `holder_repo` points at the wrong one reports `live`
forever and is never prunable. The "falsifiable predicate" is falsifiable only against a repo the
claimant nominated. Low practical risk at one call site; worth a schema comment, and worth
remembering before `holder_kind` is extended.

### W-5 [WEAKNESS] The falsification condition is stated but not measurable

`07:1724-1726`: *"If that number starts climbing, this design has failed and should be reverted, not
patched."* There is no baseline (the table starts at 0), no threshold, no cadence, and no owner.
The instrument is one line in `codebugs summary` (`07:1507`) that a human must happen to read.
§13.6's only hard assertion is a **single manual** setup→finish cycle leaving zero live claims — a
point measurement at t=0, not a trend.

Compare what it is guarding against: the 41 stale `in_progress` cards accumulated precisely because a
number nobody watched drifted, in a field nobody read. The falsification condition is therefore
protected by the same mechanism whose failure created the problem. A `divergent`/live-claim count
asserted in CI, or a `claims-audit` invocation in an existing nightly, would make it real. As written
it is an intention.

### W-6 [WEAKNESS] The architect's own closing doubt is the load-bearing one, and it is under-weighted

`07:1753-1761`: *"Whether `holder_kind='branch'` is the right identity for the general case, or only
for the one call site that has a branch in scope."* This is correct and it is bigger than §18's
five ranked risks. §11.1 makes `pull_next` claim with `holder_kind="agent"` (`07:1141`) — no external
referent, `unverifiable`, today's precision. `pull_next` is the *only* in-repo consumer that actually
writes claims (§13.5 items 2 and 3 are read-only). So on the "shell diff does not land" branch —
which §18 lists as Risk 2 — **100 % of claims in the table are `agent`-kind and L3 covers none of
them**, leaving the design with `renewed_at` plus a reader-chosen threshold, i.e. §1's D3 unrepaired.
The document says this in the last paragraph rather than in §1, where the trustworthiness argument
is made.

---

## 5. The adoption argument, attacked (question 3)

### STRENGTH — the reversal is substantively correct, and making it was the right call

Verified by direct read of `/home/faxik/w/autosorter/tools/worktree-setup.sh`:

```
:75-79   CB_IDS=$(printf '%s' "${BRANCH_NAME}" | grep -oiE 'cb-?[0-9]{3,}' | … | sort -u)
:86-88   others=$(git -C "${REPO_ROOT}" branch --format='%(refname:short)' \
             | grep -iE "cb-?${num}([^0-9]|$)" | grep -vx "${BRANCH_NAME}" || true)
:90      if [[ -n "${others}" ]]; then
:103         exit 1
```

It greps **branches**, not worktree paths, and it `exit 1`s. The Round-1 record describing a
worktree-PATH check is stale. Two slugs for one card collide on this guard today. **CB-2534 is
already prevented by shipped code, and the architect volunteering that against its own interest
(`07:223-230`, `07:1711-1713`) is the most credible thing in the document.**

### The false-positive premise is TRUE — I verified it

`worktree-finish.sh` runs `git worktree remove` at `:1338` and contains **no** `git branch -d` /
`-D` anywhere (grepped this run). Integration is `git merge "${BRANCH}" --no-ff` at `:1198`. So
merged branches are never deleted, they accumulate, and every one of them blocks all future branches
for its card, forever. The premise is real and structural.

### But the conclusion does not follow. **Two independent attacks.**

**(i) `git branch --merged` answers the same question with zero new state.**

`07:1329-1331`: *"A claim record that is released at merge time is **the one thing** that can tell a
stale branch from work in flight."* That is false. Integration is `--no-ff` (verified at `:1198`),
not squash, so every integrated branch is a strict ancestor of `main` and
`git merge-base --is-ancestor "$b" main` / `git branch --merged` identifies it exactly. A one-line
filter inside the same loop the design is already patching — or a `git branch -d "${BRANCH}"` added
to `worktree-finish.sh` after the successful merge — closes the entire false-positive class the
adoption argument rests on, using information git already has, with no table, no module, no `db.py`
seam, no `entities.py` write path, and no second-repo exit-code contract.

The design spends §13.0 arguing about a git guard and never asks what git can do. This is the
sharpest available attack and the document has no answer to it.

**(ii) §1-L5 and §13.0 are in direct tension, and the design never reconciles them.**

The refined guard (`07:1381-1388`) downgrades `others` to leftovers **only** on `who-holds` exit
code **3** — *"not held, but claim history exists"* — and deliberately does nothing on exit **4**,
*"no claim record ever for this id"* (`07:1359-1365`).

§1-L5's central argument is that **`entity_claims` starts with zero rows** and is never backfilled
(`07:206-211`, `07:1558-1562`). Therefore on day one, and for every card whose lifecycle began before
this ships, `who-holds` returns **4** and the guard refuses exactly as it does today. The
false-positive fix only becomes effective for cards that have completed a full claim→merge→release
cycle through the new path — which requires §13.2 *and* §13.3 to be landed and working, and §13.3 is
F-1 above.

So the strongest adoption argument in the document is empty at launch, ramps only as the new path
accumulates history, and depends on the least reliable component of the delivery. **Is it a
rationalization for a design the user already approved?** Not quite — the premise is verified true
and the mechanism would eventually work. But it is an argument the architect reached for *after*
conceding CB-2534, and it does not survive contact with the cheaper git-only alternative or with the
design's own L5. It should not be the load-bearing justification.

### What survives as genuine justification

`07:1320-1324` items 1 and 2 hold and are not addressed by any git trick: the guard only sees
branches (an agent in the main checkout or a hand-made worktree trips nothing), and it only runs in
that one script (`fix-latest-codebugs`, a human, an MCP client, and `pull_next` all bypass it). Those
are real gaps and the tracker is the only shared place. That is a smaller claim than §13.0 makes,
and it is the one I would defend.

---

## 6. Citation audit — the dispute, adjudicated

**The architect is wrong. The council's original line numbers are correct.** This is not a close
call and it is not a matter of which copy was read.

**Evidence, `grep -n` / `wc -l` this run:**

```
/home/faxik/w/autosorter/tools/worktree-setup.sh   274 lines, ends with \n (0x0a), clean worktree
  :120   # A warning, not a refusal: ~41 cards sit in_progress and a
  :143   git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${BASE_BRANCH}"
  :208   for cb in ${_claim_ids}; do
  :209       if codebugs update "${cb}" --status in_progress >/dev/null 2>&1; then
```

**Census of every `tools/worktree-setup.sh` under `/home/faxik/w`** (46+ copies across worktrees):

| line count | copies | `worktree add -b` | `for cb in ${_claim_ids}` | "41 cards" |
|---|---|---|---|---|
| 274 | **16** | **:143** | **:208** | **:120** |
| 172 | 17 | :60 | — | — |
| 171 | 13 | :60 | — | — |
| 151 / 131 / 118 / 98 | 5 | — | — | — |
| **275** | **0** | — | — | — |

**There is no 275-line copy anywhere on this machine.** The file ends with a newline, so `wc -l`
274 means exactly 274 lines — there is no off-by-one editor artefact available as an excuse. And the
architect's claim is internally impossible: it asserts *more* total lines (275 vs 274) while placing
the same content *two lines earlier*. Those cannot both be true of the same content.

**§0.3's claim is therefore false in both directions.** `07:87-97` says *"the Round-1 line numbers
are off by two"* and tabulates `:143 → :141`, `:208-215 → :206-212`, `:120-123 → :117-125`. All
three "actual today" values are wrong; all three "cited in R1" values are right.

### Which derived claims are load-bearing

The shift is systematic (−2 below roughly line 100; the one citation *above* it, `BRANCH_NAME` at
`:38`, is **correct** — verified `BRANCH_NAME="$1"` at `:38`). Every anchor below that point is off:

| Design says | Actually | Load-bearing? |
|---|---|---|
| `07:1373` insert before `if [[ -n "${others}" ]]` at `:86` | `:90` (`:86-88` is the `others=$(…)` assignment) | **YES** — off by 4; splicing at `:86` lands inside the `others=` command substitution |
| `07:1391` replace the `:105-133` block | `:107-135` | **YES** — the deliverable's largest hunk |
| `07:1427` insert after the loop ending `:134` | loop `done` at `:136` | **YES** — the `ERR` trap placement |
| `07:1392`, `07:231` gate runs before `git worktree add` at `:141` | `:143` | **YES** — the whole point of the design |
| `07:1444` delete `:206-212` | `:208-214` | **YES** |
| `07:1449` delete the `codebugs get \| sed` pre-read at `:110-111` | `:112-113` | **YES** |
| `07:1439` `trap - ERR` before the banner at `:259` | `:259` (banner is `:258-262`) | no — stated as a "region", lands correctly |
| `07:124`, `07:136` the `in_progress` arm at `:117-125` / `:118-121` | `:119-128` | no — prose only |
| `07:100` `open)` arm at `:113-116` | `:115-118` | no — prose only |
| `07:1003` `:206 → codebugs update` | `:209` | no — prose only |
| `07:819` "only `open` cards are flipped" comment at `:203-205` | `:205-206` | no — prose only |
| `07:1254` "No --notes" comment at `:204-205` | `:206-207` | no — prose only |
| `07:1487` claim sits "65 lines AFTER `git worktree add`" | 66 (`209 − 143`) | no — the Judge's 66 was right |
| `07:1301` "I opened `:60-104`"; `07:224` `:74-104`; `07:1712` `:74-84`; `07:162` `:75-104` | guard spans `:74-105` | no, but **four different ranges for one block inside one document** |

**Verdict.** The substance the architect derived from that read is *correct* — the guard really does
grep branches (I re-verified at `:86-88`), `BRANCH_NAME` really is in scope before the worktree is
created, every `codebugs` call really is `if`-guarded or `|| true`, and `worktree-finish.sh` really
has no `codebugs` hook to attach to. The architect got the *file* right and the *numbers* wrong,
then used the wrong numbers to accuse the whole council of being wrong. That inversion is worth
stating plainly: **§0.3 is a section whose entire purpose is citation hygiene, and it is the least
accurate section in the document.** Everything it touched must be re-anchored before implementation,
and the confident-correction reflex it displays ("the brief and both adversaries cite … the file on
disk today has …") is a pattern to distrust elsewhere in the same document.

**In-repo citations, spot-checked, all correct:** `db.git_rev_parse` at `db.py:210-226` ✅;
`pull_next` at `milestones/capacity.py:167-220` with `BEGIN IMMEDIATE` at `:182`, `COMMIT` at `:213`,
`except`/`finally` at `:214-218` ✅; `register_post_add_hook` at `db.py:178-204` ✅; `db.connect` at
`db.py:492-503` with only `journal_mode=WAL` ✅; the sole `RETURNING` in `src/` at `sweep.py:313` ✅;
`_SAFE_IDENT` at `entities.py:20` defined and unreferenced ✅; `db.row_to_dict` public at
`db.py:229-240` and the CLAUDE.md `_row_to_dict` debt bullet stale ✅. **The architect's care with
in-repo facts is high; the failure is confined to the second repository.**

---

## 7. Implementability

Blocking ambiguities beyond the findings above:

### W-1 [WEAKNESS] §6.4's `list_claims` SQL cannot execute as printed

```sql
SELECT * FROM entity_claims
 WHERE (? IS NULL OR kind = ?) … AND (:include_released OR released_at IS NULL) …
```

Mixes anonymous `?` and named `:include_released` placeholders. Executed:
`sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 3, and
there are 2 supplied.` One-line fix, but it is the only statement in the document that cannot be run
as written, and it appears in a section whose point is that the reads are cheap and correct.

### Unspecified but needed

- **`_live_holder(conn, entity_id)`** is called by the terminal hook (`07:963`) and appears nowhere
  in §5.1's "every signature" list. Its behaviour when there is no live claim (`holder=None` into a
  `WHERE holder = ?`) is undefined by the document.
- **`EntityRef.set_status`'s `expected` parameter is required** (`07:873`, no default), but §6.1's
  restore path passes `expected=projected_to` and §6.1's projection passes `expected=current` read
  earlier in the same transaction. Fine — but §8.2 never says what a caller should pass when it has
  no expectation, and the "no hook, therefore no recursion" invariant (`07:896-901`) depends on
  `set_status` having exactly one caller. Nothing enforces that; it is stated as an invariant with a
  test, not a mechanism.
- **`db.txn`'s `Iterator[bool]` yield value is never consumed** anywhere in the design. Every call
  site is `with db.txn(conn):`, discarding the flag. If nothing reads it, the reentrancy is invisible
  to callers — which is fine, but then the `bool` is documentation, and S-3's ownership question has
  no channel through which a caller could ever assert ownership.
- **Exit code 2** is unassigned in §13.1's table while 0/1/3/4/5 are used. argparse itself exits 2 on
  a usage error, which a `set -e` shell caller will see and fall through `case`'s `*)` arm. Worth
  pinning.
- **`claim_id` is generated before the upsert** and discarded on `already_mine` / `held_by_other`
  (`07:661-663`). Harmless, but the `_next_id`-shaped `SELECT … ORDER BY CAST(SUBSTR(id,5) …)` runs
  on every claim attempt including pure retries.

Everything else in §§3–12 is specific enough to implement. The SQL, the outcome vocabulary, the
error tiers, and the 25 tests are unusually concrete for a design document.

---

## 8. Proportionality (question 7) — scope, measured against what was approved

The user's decision is not mine to revisit. What I can measure is drift.

| | Round 1 (B1, as approved) | Round 2 |
|---|---|---|
| `claims.py` | ~200 lines incl. tools + CLI | ~330 (architect: "would not be shocked by 380") |
| `db.py` | ~40 | ~70 (`txn`, hook pair, `busy_timeout`) |
| other source | `findings.py` ~30 | `entities.py` 21, `findings.py` 34, `reqs.py` 20, `capacity.py` 35, `merge.py` 15, `server.py`+`cli.py` 2 |
| tests | ~250 | ~400 + ~120 edits to existing tests |
| second repo | "a SKILL.md line" | ~65 lines across two scripts, a new exit-code contract, an `ERR` trap |
| CLAUDE.md | — | 5 amendments incl. **a stated module-boundary rule change** |
| **total** | **~520** | **~1050**, 2–3 focused days |

Roughly 2×, and the footprint went from 2 source files to 8 plus 2 shell scripts in another repo.

**Most of the growth is legitimate** — the core/wrapper split, soft delete, and the audit layer are
direct consequences of R1's FATAL-1/2 and SERIOUS-6/7, which the user's "build B1" implicitly
authorised fixing.

**These are not, and should be named as separable:**

- `merge.py`'s `db.txn` refactor (~15 lines) — explicitly "mechanical, behaviour-identical", touches
  a module claims never uses.
- `release_item`'s atomicity fix (§11.2) — the design itself calls it *"an atomicity bug fix
  independent of claims"* (`07:1186`). It is a real bug and should be **its own commit against its
  own codebug**, not a rider.
- `expected_status` + `changed` on `update_finding` **and** `update_requirement` + MCP + CLI (§12,
  ~35 lines + tests) — §12.2 goes to some length to prove it is *orthogonal to ownership*. That is an
  argument for shipping it separately, not together.
- `--append-note` CLI exposure, `codebugs get`'s claim block, `codebugs summary`'s line.
- The `meta` read-modify-write lost-update bug (`07:1289-1293`) — correctly identified, correctly
  scoped out, correctly says "it should be filed". File it.

§16's sequence (`07:1673-1677`) already lands `busy_timeout`, `db.txn`+refactor, and
`entities.set_status` as independent commits, and says so deliberately. That is the right instinct;
it should be extended to the four items above. **Finding, not insubordination: the approved decision
was "build the real field". `expected_status`, the `merge.py` refactor, and the `release_item` fix
are three separate improvements riding on that approval.**

---

## Did Round 2 Fix Round 1?

| # | Round-1 defect | Verdict | Evidence I opened/ran this run |
|---|---|---|---|
| **FATAL-1** | nested txn in `pull_next` | **FIXED** | core/wrapper split at `07:462-487`; `capacity.py:214-216` handler read; `in_transaction=True` after `BEGIN IMMEDIATE` executed. Reentrancy correct for the intended case — see **S-3** for the case it misses, and **S-4** for the boundary rule it breaks |
| **FATAL-2** | `rowcount` on `RETURNING` | **FIXED** | all 8 statements re-checked against §6.1/6.2/6.3/§8.2/§10.2; `fetchone()`/`fetchall()` throughout; my probe reproduces `(None, rowcount 0)` on the other-holder path. Plus a genuine bonus find (`prev_status` carry-forward) |
| **SERIOUS-3** | `undetermined` self-masked | **FIXED at the root** | executed: BUSY=5, LOCKED=6, ERROR=1, BUSY_SNAPSHOT=517; nested BEGIN and rollback-no-txn both code 1; `(code & 0xFF)` classifier correct |
| **SERIOUS-4** | `db.py` seam, one consumer | **JUSTIFIED** | `register_post_add_hook` at `db.py:178-204` confirmed symmetric. Argument #1 is self-undermined by §13.2(d) deleting the live call site |
| **SERIOUS-5** | hook findings-only | **FIXED** | both `findings.py:298/299` and `reqs.py:222/223` confirmed to have their own commits; hook fires at both |
| **SERIOUS-6** | non-additive PK upgrade | **FIXED (retracted + redesigned)** | executed the exact schema: partial unique index gives identical exclusion, closed rows coexist |
| **SERIOUS-7** | audit fact with no home | **FIXED** | soft-delete columns + `GROUP BY release_reason` health check |
| **MEDIUM-8** | callback registry vs declaration | **FIXED** | `entities.py:23-33` fields all default-free and `ENTITY_KINDS` constructed by keyword → trailing defaulted field is back-compatible as claimed. **But introduced S-1** |
| *(unnumbered)* | **requirements projection** — Codex FATAL #1, Opus X-4 | **NOT ADDRESSED, and omitted from the ledger** | still `busy_status=None` at `07:856`; labelled "SETTLED" with no settlement in `CHECKPOINT-r1.md` |

No regressions against Round 1's fixed items. All eight self-nominated fixes are real.

## New Problems Introduced

| ID | Sev | Problem |
|---|---|---|
| **F-1** | **FATAL** | §13.3's release block aborts `worktree-finish.sh` on its normal (no-match) path — `set -euo pipefail` at `:11`, `grep` exits 1, `\|\| true` is on the wrong command. Breaks branch integration for everyone |
| **F-2** | SERIOUS | `ERR` trap misses `exit` and `SIGINT`, and is installed after the loop whose `exit 1` it must cover. The guard on the design's own largest risk does not work |
| **F-3** | SERIOUS | Every anchor in the §13.2/13.3 diff is off by 2–4 lines (see Citation Audit) |
| **S-1** | SERIOUS | `entity_terminal` gated on `project and busy is not None` → unreachable for all requirements and under `--no-project`; §11.1's advertised bug fix is false for `item_kind='requirement'`; exit code 4 dead for FR ids |
| **S-2** | SERIOUS | `pull_next` gains a `KeyError` path on orphaned `item_ref`s via `ref.require()`; the design's own §3.2 cites `triage.py` catching that exact case. Undisclosed |
| **S-3** | SERIOUS | `db.txn` treats any ambient transaction as owned; the design's own probe P8 proves legacy `isolation_level=''` creates ownerless ones. Public `claim()` can then write without ever committing. Inherited by every future caller once CLAUDE.md mandates `db.txn` |
| **S-4** | SERIOUS | `capacity.py → claims._claim_core` is a private cross-module call, which CLAUDE.md forbids verbatim; §2.1 asserts the opposite about this exact dependency |
| **S-5** | SERIOUS | The requirements-projection finding — the only one both adversaries scored FATAL/SERIOUS against every proposal — is absent from §17 and relabelled "SETTLED" without a citable settlement |
| **W-1** | WEAKNESS | §6.4 `list_claims` mixes `?` and `:named` params — `ProgrammingError`, executed |
| **W-3** | WEAKNESS | §1's headline `--verify` predicate is not expressible through `db.git_rev_parse`; §10.1 silently drops it |
| **W-4** | WEAKNESS | `holder_repo` caller-supplied, unvalidated, in a cross-project tracker |
| **W-5** | WEAKNESS | The falsification condition has no baseline, threshold, cadence or owner — guarded by the same "someone will notice a number" mechanism that produced the 41 stale cards |
| **W-6** | WEAKNESS | If §13.2 does not land, 100 % of claims are `holder_kind='agent'` and L3 covers none of them — the trustworthiness argument collapses to `renewed_at` + a threshold. Stated only in the final paragraph |

### Strengths worth recording

- **The CB-2534 retraction** (`07:223-230`, `07:1711-1713`). Verified correct at `:86-88`/`:103`.
  Giving up your own headline adoption claim, against interest, is the most credible act in the
  document.
- **Executed-not-asserted.** I reproduced P1/P2/P3/P5/P6/P7 independently; every one held. The
  soft-delete + partial-unique-index substrate does exactly what §3.1 says.
- **Index claims hold at scale.** 200 000 rows / 200 holders: `who_holds` 0.05 ms via
  `idx_claims_live`, `held_by` 0.19 ms via `idx_claims_holder_live`. Criterion 3 met, no 752 ms fold.
- **The two-call `git_branch_verifier`.** Probing `HEAD` before the branch is the difference between
  a prunable signal and a claim-shredder on any machine without git. Correct and non-obvious.
- **In-repo citation accuracy** is high; every one I spot-checked was right.
- **§18 and §1's "converse is the honest risk"** name the design's own largest failure mode above
  every technical defect. Rare and correct.

## Citation Audit (adjudicated)

**The architect is wrong; the orchestrator and Round 1 are right.**
`worktree-setup.sh` is **274 lines** (trailing newline present, so no off-by-one is available),
`worktree add -b` is at **:143**, `for cb in ${_claim_ids}` at **:208**, `codebugs update` at
**:209**, the "~41 cards" comment at **:120**. No copy of this script anywhere under `/home/faxik/w`
has 275 lines or places `worktree add` at :141 — 16 copies at 274 all agree, and the older 171/172
copies place it at :60. §0.3's "off by two throughout the council" is false, and its claim of *more*
total lines with *earlier* content is internally impossible.

**Load-bearing consequences:** six insertion anchors in §13.2 — `:86`, `:105-133`, `:110-111`,
`:134`, `:141`, `:206-212` — are all wrong (correct: `:90`, `:107-135`, `:112-113`, `:136`, `:143`,
`:208-214`). Since §13.2 is the deliverable the design says decides whether the rest matters, the
whole diff must be re-anchored. The remaining ~8 shifted citations are prose and cost nothing.
Separately, the guard block is given **four different line ranges** in one document (`:60-104`,
`:74-104`, `:74-84`, `:75-104`).

**The substance survives.** The guard really does grep branches; `BRANCH_NAME` really is in scope at
`:38` (correct citation); every `codebugs` call really is `if`-guarded or `|| true`;
`worktree-finish.sh` really has no `codebugs` hook and really never deletes branches. The architect
read the right file and mis-numbered it — then used the mis-numbering to correct everyone else.

## Readiness: **NOT READY**

Three blockers, in order:

1. **F-1** — §13.3 as written aborts `worktree-finish.sh` on its expected path, after the merge. This
   is a one-token fix (`|| true` on the pipeline) but it must not ship as printed, and it is in the
   half of the delivery the design itself calls decisive.
2. **F-3 / §0.3** — every insertion anchor in the shell diff points at a file that does not exist.
   Re-anchor §13.2 and §13.3 against the real 274-line file and correct §0.3's accusation on the
   record, so the next reader does not inherit it.
3. **S-1 + S-2 + S-3 + S-4** — four defects in the *module* half, each individually small and each
   currently invisible because §17 reports the area as fixed. S-1 is a one-line de-indent; S-4 is a
   rename; S-2 needs a `try/except KeyError: continue`; S-3 needs either an ownership token on
   `db.txn` or a documented precondition. None is architectural. Together they mean the design cannot
   be handed to an implementer as-is.

**And one decision the user should make explicitly rather than inherit:** S-5. Requirements still do
not project, the brief still requires it, and both Round-1 adversaries called that out. Whatever the
answer, it should be a recorded ruling and not a "SETTLED" label the architect applied to itself.

The substrate is sound — the SQL is verified, the transaction discipline is correct, the error
classification is right, and the retractions are honest. The gap is entirely in the delivery half and
in four small module defects. **ALMOST, if F-1 and F-3 were the only issues; NOT READY because S-1
through S-4 sit inside the sections §17 certifies as fixed, which means the ledger cannot currently
be trusted as a completion signal.** One more focused pass should close all of it.

