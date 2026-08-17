# CB-81 — a well-typed CSV kills `bench-import` with a raw `IntegrityError`

Branch `fix/cb-81-import-pre-write-validation`, base `a4bc51a`.
Card: CB-81, `medium`, `error_handling`, `src/codebugs/bench.py`.

**Revision 2, after `adversarial-review-x2` (Opus adversary + Codex/Sol, in parallel). Revision 1's
central framing was measurably false and is corrected below rather than quietly dropped.** The
corrections appendix at the end records what each attacker caught.

## Ranking — why this card, and why alone

Focus is `codebugs`, restricted by the operator to *pure bugfixes, simplest first, batched where
possible*.

**The discriminator is NOT the `needs-decision` tag** — revision 1 used it to disqualify CB-34,
CB-29 and CB-33 while CB-81 carries it too, which is a contradiction seven lines wide (Opus S1;
14 of the 35 open cards carry the tag, so it separates nothing). The honest discriminator: CB-81's
"decision" is a choice between two *engineering* options that its own card states and that turn out
to be complementary rather than exclusive — pre-write validation and a transaction — with no
user-visible semantics riding on the answer. CB-34 ("should a resolved entity's blocker rows be
cancelled, or filtered at query time?"), CB-29 ("should `category=0` raise, coerce, or stay?") and
CB-33 ("which of several milestone attachments is *the* one?") each require an answer that changes
what the product means. That is the line, and CB-81 is on the buildable side of it.

The remaining runners-up: **CB-86** is explicitly `unreproduced` and needs a new error classifier
whose design the claims layer constrains; **CB-68** is a missing `*` in a signature — mechanical,
but a convention violation, not a defect; **CB-78** is the POSIX stdout-semantics question.

**Batching is rejected — and revision 1's authority for that was invented.** It cited
`.claude/plans/BATCH-codebugs-dryrun-2026-08-17.md` as having run "the clustering exercise over this
exact backlog". Both attackers checked: that file is **untracked in main** (so it is not in this
worktree and will not travel with the merge), it covers **24** rows and not 35, and it mentions
neither CB-77 nor CB-81 — `grep -c "CB-77\|CB-81"` → 0, `grep -n hostage` → nothing. Its "do not
batch" conclusion is real but was reached over a different population.

The rejection therefore stands on the hostage test applied here, to these two cards: **CB-77** (a
read failure mid-CSV-import leaves committed rows behind) is a different function, a different
table, and *its own card* says the fix is a decision between one outer transaction and explicit
partial-success reporting. If it were in this tree, a finished CB-81 would sit waiting on that
decision. Two trees.

What *is* batched here is one transformation closing several symptoms — clustering predicate 1,
same root cause.

## Reproducer — and the correction that matters

`/tmp/…/scratchpad/repro81.py` plus a real CLI run in a throwaway tracker. **Two columns, because
conflating them is what produced revision 1's false headline:**

| payload | library harness (in-memory conn, never closed) | **shipping surface (`codebugs bench-import`)** |
|---|---|---|
| `m,v\na,1\na,2\n` (duplicate row labels) | `IntegrityError` · `in_txn=True runs=1 results=1` | **raw traceback**, exit 1 · `runs=0 results=0` |
| `m,v,v\na,1,2\n` (duplicate headers) | same | **raw traceback**, exit 1 · `runs=0 results=0` |
| `m,v\na,nan\n` | `IntegrityError: NOT NULL … value` | **raw traceback**, exit 1 · `runs=0 results=0` |
| `m,v\na,inf\n` | succeeds, stores `inf` | succeeds, stores `inf` |
| `m,v\na,1\n,2\n` (empty label) | `ValueError` · `runs=1 results=1` | clean message, exit 1 · `runs=0 results=0` |
| same explicit `run_id` twice | `IntegrityError` on `codebench_runs.run_id` | **not reachable** — see below |

**`runs=1 results=1` is an artifact of the harness, not user-visible state.** `db.connect()` returns
`isolation_level=''` (legacy implicit transactions), so the INSERTs sit in an uncommitted implicit
transaction, and both shipping callers discard it: the CLI handler ends in `finally: conn.close()`
(`bench.py:894-895`) and the MCP wrapper uses `with conn_factory() as conn` (`bench.py:752`), whose
`__exit__` rolls back on an exception. Measured on both paths. Revision 1 asserted that the existing
`ValueError`s "arrive with rows already inserted" and were therefore a live CB-15/CB-16
success-shaped lie **reachable today** — that claim is false and is withdrawn. The card's own body is
careful here ("the caller is left holding a **partial uncommitted** state"); the card's *title* is
not, and should be corrected when the card is closed.

**`--run-id` is not a `bench-import` flag** (`bench.py:1001-1007` declares `file`, `--json-file`,
`-b/--benchmark`, `--date`, `--tags`, `--meta`; the MCP wrapper does not expose `run_id` either), so
revision 1's "fifth user-visible door … `bench-import --run-id BE-9` twice" was a reproduction that
does not run. `argparse` exits 2. The explicit-`run_id` collision is **library/test-reachable only**,
and the fix keeps a guard for it on that honest basis, not as a user-facing door.

### So what is actually defective

1. **A raw `sqlite3.IntegrityError` traceback from an ordinary data file.** Real, user-visible,
   exit 1 with a stack trace — CB-81 defect (1), and the same shape as CB-71.
2. **`inf` imports silently and stores a non-measurement**; `nan` is refused only by accident, by a
   `NOT NULL` constraint, in constraint vocabulary. CB-81 defect (3).
3. **Atomicity is accidental.** Nothing rolls the partial import back on purpose — it survives only
   because both callers happen to discard the connection. A library caller on a long-lived
   connection keeps the partial state, and `import_csv` is a 20th instance of the CB-24
   read-modify-write shape (`_next_run_id` reads the max `BE-n`, the caller then inserts `BE-n+1`,
   nothing locks between) that CB-36's "19 instances" sweep missed. CB-81 defect (2), correctly
   scoped.

## Root cause

`import_csv` (`bench.py:165-301`) interleaves **validation with writing**. It validates the
non-payload arguments up front (CB-82's fix), then inserts the run row at `:265`, then validates
each cell *inside* the loop that inserts it at `:286`, and ends in a bare `conn.commit()` at `:293`.
So every payload fault below the first two columns is discovered after writes have started, and the
schema's constraints are the only thing checking the payload — which means the failure arrives as an
`IntegrityError`, a class no arm in `cli.py` handles.

## The fix

**Every fault the payload can carry is decided before the first INSERT, and every write plus the
reads that feed it happen inside one `db.txn`.** Both halves are required. Pre-validation alone
leaves the run-id race and any bind-time or environmental fault mid-loop; the transaction alone
still reports payload faults in constraint vocabulary.

1. **A pre-pass over the parsed rows** builds the exact `(row_label, metric, value)` triples that
   will be written, and refuses with a `ValueError` naming the row/column:
   - an empty row label (moved out of the write loop, message unchanged);
   - a non-numeric metric (moved out of the write loop, message unchanged);
   - a **non-finite** metric;
   - a **duplicate `(row_label, metric)` pair**.
2. **`with db.txn(conn):`** wraps `_next_run_id`, the run-id existence check, the run INSERT and the
   result INSERTs. The bare `conn.commit()` is deleted — `db.txn` commits, and yields `False` under
   an ambient transaction so a caller that owns one keeps owning it (CB-24 consequence 1).
3. **A run-id already present** raises a `ValueError` naming it, checked inside the transaction.
   This covers the library-reachable explicit-id collision *and* the generated-id collision Codex
   found: `_next_run_id` orders by `CAST(SUBSTR(run_id,4) AS INTEGER)`, which saturates at
   2^63−1, so ids beyond that range tie and the increment can in principle land on an existing row.
   Checking the final `rid` covers both origins without relying on that analysis being exhaustive.
4. **`list(reader)` is wrapped so a malformed CSV raises `ValueError`.** `_csv.Error` is **not** a
   `ValueError` (measured), so today `field larger than field limit (131072)` escapes both
   `_cmd_bench_import`'s arm and `cli.main` as a raw traceback from a plain data file — the same
   user-visible symptom as defect (1), through a door the card did not enumerate. Rewrapping is safe
   here *because it is pre-write*: it cannot convert a post-commit failure into "bad input", which is
   the reason `import_csv`'s docstring gives for refusing a blanket rewrap elsewhere.

### Why the duplicate check is on `(row_label, metric)` pairs, not labels or headers

The card's literal wording says "validate the header for duplicates and the rows for label
collisions". Refusing either *by name* would break payloads that import cleanly today, both measured:
`m,v,w` with rows `a,1,` and `a,,2` repeats the label and writes 2 disjoint values; `m,v,v` with
`a,,` has a duplicate header and writes 0. The pair check is **the UNIQUE constraint evaluated
earlier**, so it narrows nothing and still catches both of the card's cases, because both actually
collide. The Opus adversary ran a 27-case differential against this specifically — duplicate
fieldnames, short rows (`restval=None`), long rows (`restkey`), whitespace cells and labels, empty
metric names, NFC/NFD, `1` vs `1.0` — and every case that imports today still imports with
byte-identical rows. This is the letter-vs-intent split: the card's *intent* is "no `IntegrityError`
from a payload", and the pair check serves it where the card's *letter* would have over-refused.

### The deliberate narrowing — the full list, not the card's

Refused after this change, all of which import successfully today: **`inf`, `-inf`, `Infinity`, and
any literal that overflows `float()` to infinity, such as `1e400`** (Codex/Opus S3 — revision 1
enumerated only the first three, and a CHANGELOG copying that list would understate the change).
`nan` is also refused, but it already fails today, in the wrong vocabulary and after a write.

This is inside the card, which says "validate … **non-finite metrics** BEFORE the first INSERT", so
it is not a CB-82-style ride-along. `_require_json_text`'s NaN/Infinity policy for `meta` is
**unchanged** and stays declined, and that asymmetry is deliberate: a metric is a measurement, a meta
value is opaque caller data. Both attackers probed this; the Opus adversary confirmed the existing
policy is already pinned by `tests/test_bench.py:906-916`.

## Scope of the atomicity guarantee — stated, not implied

`db.txn` yields `False` under an ambient transaction and issues no `BEGIN IMMEDIATE`, so **"nothing
is written on refusal" and "the run-id check is not racy" hold on a connection with no open
transaction**. Under ambient ownership the caller keeps both the partial writes and the unserialized
check — correct for an import (it is not an acquisition, so unlike `merge.merge` and
`capacity.pull_next` it must not `raise` on ambient), but it is a scope, and an unscoped guarantee is
how this repo gets a "gate that cannot fire" note. Every new test states which connection state it
asserts over.

## What is deliberately NOT done

- **The CLI arm is not widened to catch `IntegrityError`** — the card forbids it as a substitute, and
  after this change no payload fault raises it. A residual `IntegrityError` now rolls back. The
  `cli.main` boundary question is **CB-78**.
- **A lone surrogate in a label** (`"\ud800"`) fails at *bind* time, which a pre-pass over Python
  strings structurally cannot see. It is a `UnicodeEncodeError`, which **is** a `ValueError`
  subclass, so the CLI already reports it as one tidy line — and after this change it rolls back
  first, making that report honest. This is independent evidence for "both halves are required", and
  it is why the fix claims *every fault the pre-pass can see*, not exhaustiveness.
- **`bench.delete_run` (`:671`) and `bench.delete_benchmark` (`:704`)** still end in a bare
  `conn.commit()`, and CB-36 recorded the second as "a genuine TOCTOU against a concurrent
  same-benchmark import". This change hardens exactly one side of that race. Fixing the other side
  is out of scope; a follow-up card is filed, and it must also record that CB-36's "19 instances,
  all fixed" tally is now wrong — this repo's own enumeration lesson landing on the card written to
  teach it.
- **`import_json` is not touched**: it converts to CSV and delegates, so it inherits every guard. One
  test pins that inheritance.
- **`findings.py`'s import loop (CB-77)** — separate tree.

## Risks

- Hoisting `from codebugs import db` to `bench.py`'s top level: **verified clean** — `bench.py`
  already imports `db` at `:713`, and `db.py`'s only bench reference is inside
  `_ensure_modules_loaded()`.
- `tests/test_claims.py::test_24` counts raw `BEGIN` literals; `db.txn` adds none, so there is no
  allowlist to join.
- CB-24 consequence (2) is satisfied without action: the return dict (`:294-301`) is built from
  Python locals, with no `row_to_dict`/`json.loads` to move after the block.
- Moving validation ahead of the writes changes **which** fault a multi-fault payload reports first.
  Tests pin the messages that exist today for the moved checks.
- `db.txn` takes the write lock for the whole parse-and-insert rather than at the first INSERT. For a
  bulk import that is the correct scope, but a large import now holds it longer; not measured
  against `busy_timeout=5000` under real WAL contention.
- The pre-pass retains every non-blank triple in memory. Rows are already materialized by
  `list(reader)`, so this is a constant-factor increase, unmeasured for very wide imports.
- `codebench_import`'s docstring says "numeric values" (`:733-734`); it becomes "finite numeric", so
  `tests/golden/mcp_schema.json` must be regenerated deliberately.

## Verification

In the worktree, `uv run --extra dev`. **The discriminating assertion is the exception type and
message, not the row counts** — `runs=0 results=0` is already the CLI outcome today, so a
count-only check passes against the unfixed tree (Opus W2).

1. One test per payload fault, each proven to fail against `git show main:src/codebugs/bench.py`:
   duplicate pair, duplicate effective header, `nan`, `inf`, `1e400`, empty label after a valid row,
   non-numeric after a valid row, malformed CSV, duplicate run id.
2. A parameterized **no-write** assertion over every formerly mid-write branch, on a **clean**
   connection, asserting `runs`/`results` are unchanged from a snapshot taken before the call —
   never "the tables are empty", because the duplicate-run-id case requires a first successful
   import (Codex; Opus W2).
3. **Ambient-transaction test**: a valid import plus an unrelated pending write on the same
   connection; assert `in_transaction`, roll back, and verify both disappear — i.e. `import_csv`
   did not commit the caller's work.
4. **Commit test**: a successful standalone import is visible from a *second* file-backed
   connection. Reading back through the same connection does not prove a commit.
5. **Injected mid-loop failure** on the second result INSERT; assert both tables roll back. Payload
   tests alone do not prove protection from an environmental failure.
6. **Ordering test**: `BEGIN IMMEDIATE` is issued before `_next_run_id`'s SELECT, captured with the
   repo's `RecordingConnection` pattern. Without it, moving only the INSERTs inside the transaction
   passes every functional case while leaving the race.
7. **CLI integration test**: exit 1, no traceback on stderr, persistent state unchanged.
8. **`import_json` inheritance test** reaching the new guards through a valid JSON document.
9. A preservation test that a successful import is byte-identical, **labelled as such** — it passes
   before and after by design (CLAUDE.md permits this only when the docstring says so).
10. Full suite + `ruff check src/ tests/`; `CHANGELOG.md` entry; regenerate the wire golden with
    `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`.

## Adversarial Review x2 — corrections applied

| # | Finding | Source | Disposition |
|---|---|---|---|
| F1 | "After rows have landed" is false on both shipping surfaces | **Opus** (Codex missed) | CONCEDED, re-measured myself, framing rewritten |
| F2 | `--run-id` is not a CLI flag; the "fifth door" reproduction does not run | **Opus** (Codex missed) | CONCEDED, claim withdrawn, guard rescoped |
| F3 | The batching authority is untracked, covers 24 rows, never mentions CB-77 | **both** | CONCEDED, argument rebuilt on the hostage test |
| S1 | Ranking disqualifies others on a tag the pick also carries | **Opus** | CONCEDED, real discriminator stated |
| — | Verification expectations internally impossible for the run-id case | **both** | CONCEDED, snapshot-based assertions |
| — | `_csv.Error` is not a `ValueError`; raw traceback survives | **both** | CONCEDED, rewrap added to scope |
| — | `1e400` overflows to `inf`; narrowing list understated | **both** | CONCEDED |
| — | Generated run ids can collide via `CAST` saturation | **Codex** (Opus missed) | CONCEDED, existence check covers both origins |
| — | FK on `results.run_id` is not enforced (`foreign_keys=0`) | **both** | CONCEDED, no longer cited as a backstop |
| — | CB-36's closed tally is wrong; two bench TOCTOU sites remain | **Opus** (Codex missed) | CONCEDED, follow-up card |
| — | Ambient-transaction guarantee stated unconditionally | **Opus** | CONCEDED, scope stated |
| — | Surrogate label escapes the CLI arm as a traceback | Codex | **REJECTED, measured**: `UnicodeEncodeError` *is* a `ValueError`, so the arm catches it; the transaction makes the report honest |
| — | Refusing `inf` is an unauthorized product decision | Codex | **REJECTED**: the card's body says "validate … non-finite metrics"; Opus independently confirmed it is inside the card |
| — | "A separate process bypasses `BEGIN IMMEDIATE`" | Codex | ACCEPTED as a correction to revision 1's wording; SQLite's write lock is cross-process |

Both attackers ran read-only against the real tree. Cross-model value on this review was **real and
asymmetric**: Opus found the two FATALs that invalidated the framing (it ran the actual CLI; Codex
reasoned from source), Codex found the `CAST` saturation and pushed hardest on test vacuity. Neither
alone would have produced this revision.
