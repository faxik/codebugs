# CB-79 — `OSError` from sources the "guard the `open()`" sweep cannot see

**Card:** CB-79 (`low`, `error_handling`, `src/codebugs/reqs.py`), filed 2026-08-17 out of the
adversarial review of CB-71's plan. **Branch:** `fix/cb-79-ambient-oserror-sources` · **Base:** `4ee8c6c`

## Why this card, alone

Focus is `codebugs`, "pure bugfixes I can confidently fix, simplest first". CB-79 is the next
candidate the previous iteration named. It needs no product decision, both halves are reproduced
below by running them, and the repo already contains the pattern for each half — so this is
conformance work, not design.

**Not batched.** CB-76 (landed) was the nearest neighbour and was split from this on the same
grounds the clustering rules require: its transformation is *hoist a write into an atomic helper*,
this one's is *widen an except tuple* and *guard an ambient call*. Two edits here, both inside this
one card, so no clustering predicate is needed.

## Reproducers — both halves, on `4ee8c6c`, by running them

### (1) a deleted cwd → raw traceback

```
$ codebugs --tracker-root <repo> reqs-verify     # from a directory that was rmdir'd
  File "src/codebugs/reqs.py", line 442, in verify_requirements
    root = project_dir or os.getcwd()
FileNotFoundError: [Errno 2] No such file or directory
```
`_cmd_reqs_verify` (`reqs.py:1003`) has **no `try` at all** — so it also leaks the connection on
any exception, exactly as `_cmd_reqs_import` did before CB-71 fixed it.

### (2) a non-executable git → `PermissionError` escapes the tuple

```
PATH=<dir holding only a chmod-000 git>
provenance.file_status(...)  ->  PermissionError: [Errno 13] Permission denied: 'git'
```
**A negative result worth recording, because the first attempt at this reproducer failed:** putting
a `chmod 000 git` *earlier* on `PATH` does **not** reproduce it. CPython's exec continues the `PATH`
search on `EACCES`, so it silently finds the real git and the call succeeds. The non-executable git
must be the **only** one on `PATH`.

## Root cause, and the population is larger than the card's list

Two shapes, and the card named one site of each. Sweeping for the **shape** rather than the card's
line numbers — which is this card's own stated lesson — finds more:

**Shape A: a `subprocess` call guarded by `(subprocess.SubprocessError, FileNotFoundError)`.**
`FileNotFoundError` covers *git is missing*; it does not cover *git is not executable*
(`PermissionError`) or *the cwd handed to `subprocess` no longer exists* (`NotADirectoryError` /
`FileNotFoundError` on the cwd, which is a different errno path). **Five sites, not one:**
`provenance.py:43`, `:54`, `:88`, `:225` — and **`db.py:534` (`git_rev_parse`), which the card does
not mention at all.**

**Shape B: an ambient `os.getcwd()` that can raise.** Four sites: `provenance.py:30`, `:120`,
`:215`, `reqs.py:442`. `db.py:876-882` **already fixed exactly this** and carries the comment
explaining why ("Escaping, that would bypass every `DatabaseNotFoundError` handler — a traceback in
the CLI"), so the pattern is house style; these are simply the sites its sweep could not name.

## Plan — two independently landable edits

### Edit A — widen the five tuples (mechanical, no decision)

`(subprocess.SubprocessError, FileNotFoundError)` → `(subprocess.SubprocessError, OSError)`.

`FileNotFoundError` is a **subclass** of `OSError`, so this is a strict widening: nothing previously
caught stops being caught, and no existing behaviour changes except that three more errno cases stop
escaping. `subprocess.SubprocessError` is **not** an `OSError` subclass, so it must stay in the
tuple — dropping it would lose `CalledProcessError` and `TimeoutExpired`.

### Edit B — guard the four `os.getcwd()` calls, each in its OWN module's idiom

This is the one judgement call in the card, so it is stated rather than assumed: **degrade where the
function already has a vocabulary for "git could not be consulted", raise where it does not.**

| Site | Behaviour on a lost cwd | Why |
|---|---|---|
| `provenance._parse_trailers` (`:215`) | return `[]` | Its docstring **already promises** "returns `[]` if git is unavailable". A lost cwd is a strict case of that. |
| `provenance.file_status` (`:30`) | `{"file_status": "unknown", "reason": "no_cwd"}` | The function's whole return vocabulary is `current/modified/renamed/deleted/unknown`, and `unknown` already carries a `reason`. Note this is **not** the repo's "a guard reporting clean because it could not look" failure — `unknown` is precisely *not* clean. |
| `provenance.check_findings` (`:120`) | same degradation, passed down | `cwd` feeds `git_rev_parse(silent=True)` and `project_dir=` on the per-finding calls; both already degrade. |
| `reqs.verify_requirements` (`:442`) | **raise `ValueError`** naming the remedy | It has **no** degradation vocabulary — `root` is used once, at `reqs.py:477`, to locate `tests/`. Silently reporting "no issues" because we could not look *would* be the failure the rule above names. |

And `_cmd_reqs_verify` gains the house arm plus `finally: conn.close()` — matching what CB-71 landed
for `_cmd_reqs_import`.

## Verification

Group A (must fail against `4ee8c6c`): `reqs-verify` from a deleted cwd is one clean line, not a
traceback (discriminator: **`"Traceback" not in stderr`** — exit 1 does not discriminate, since an
uncaught traceback also exits 1); `file_status` with only a non-executable git on `PATH` returns
`unknown` instead of raising `PermissionError`.

Group B (compatibility pins, green both sides, labelled): a missing git still degrades exactly as
before at every widened site; `CalledProcessError` and `TimeoutExpired` are still caught; a normal
`reqs-verify` is unchanged.

Mutation-check every widened tuple and every guard, verifying each mutation **landed** before
believing a "didn't fail" row.

## Risks / out of scope

- Not touching the `open()` sites (CB-71, landed) or the export side (CB-76, landed).
- Not making `provenance` raise on lost cwd — degrading is the module's existing contract, and
  changing it would alter the MCP surface for `staleness_check`.
- No new module, no new API; `db.git_rev_parse`'s signature is unchanged.

## Review scope, stated rather than skipped

Scaled **down** from `adversarial-review-x2` to a single bounded Codex pass, deliberately: Edit A is
a strict superset with no decision in it, and the only judgement is Edit B's degrade-vs-raise split,
which is one question. The standing rule's full two-attacker round is for a design surface; this
adds none. Saying so because "I skipped review" must be a visible choice, not a silent one.
