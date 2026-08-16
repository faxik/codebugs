# CB-45 — Similarity Extension + Pre-Add Resolver Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Status: REVISED after adversarial-review-x2 (health 6.5/10, 14 mandatory fixes — all encoded
below). Review appendix at the bottom. The ratified scope survived every attack untouched; the
fixes reshape the transaction contract, the module boundary, the report's return type, and the
firing rule's factual basis.

**Goal:** An annotate-only similarity layer over the CB-43 identity substrate: new findings get
`meta.similar_to` candidates stamped at file time, an offline scrub produces the auditable
grouping report CB-46 needs as its dry run, and the pre-add resolver seam is designed and built
against this as its first real consumer.

**Architecture:** A new zero-dependency module `similarity.py` — the FIRST self-registering
non-domain module in the package; what keeps that legal is that it issues **zero SQL**: all row
access goes through a new public findings-owned accessor `findings.similarity_candidates`
(the CB-28 owning-domain shape). It plugs into a new pre-add resolver seam in `db.py`
(per-resolver SAVEPOINT, annotate-only outcome, an enforced never-commit contract, failures
stamped queryably into `meta.resolver_errors`). The `embeddings.py` citation is narrow and
deliberate: it precedents caller-supplied vectors ONLY (it is a reqs-imported delegate, not a
self-registering extension — the adversarial review corrected an earlier overclaim here).

**Tech Stack:** Python 3.11+, sqlite3, stdlib only. No new dependencies.

**Spec:** This document, §Design below (self-contained).

## Global Constraints

- No new runtime dependencies; detector is deterministic stdlib code.
- Codebugs stays model-free: embedding vectors are always caller-supplied.
- ANNOTATE, NEVER AUTO-MERGE: no similarity outcome may change identity routing, statuses, or ids.
- Resolvers never commit — ENFORCED, not documented (D2); per-resolver SAVEPOINT; failures
  queryable, not stderr-only (CB-45 card, ratified scope item 4).
- `similarity.py` issues no SQL against any domain table — findings owns its own reads.
- All standing repo rules apply (db.txn, RETURNING rule, one-assignment-per-column, vocabulary
  resolvers on both sides, error-arm ordering, prove-fail-first tests).
- Line length 100, `uv run ruff check src/ tests/` clean, full suite green.

---

## Design (★ = decision made here; reviewed by adversarial-review-x2, verdict encoded)

### D0. Notification obligation (mandatory fix 13 — DONE, remains on record)

The CB-45 card's ratified target "a 0.95 similarity ratio collapses the 115-row family" was
measured false and replaced under the letter-fix protocol. The one-line notification delivered to
the user (2026-08-16 session log): *"CB-45's ratified 0.95 was measured and rejected: at 0.95
only 77 rows collapse and the target family does not unify; at 0.7 it splits into ~10 coherent
subfamilies (111/115 grouped). Shipping 0.7; the 'one 115-row family' target is dropped as a
false merge."* Work proceeds without waiting (the standing rule's protocol).

### D1. Measured reality (2026-08-16, real autosorter corpus, 3162 rows at design time)

Method: normalize every description with the shipped `_normalize_for_fingerprint`, char-trigram
Jaccard, union-find within category blocks.

- Exact identity baseline: 15 multi-row fingerprint families, **71 rows collapse**.
- Trigram Jaccard @ **0.7**: 11 multi-row families, **102 rows collapse**. The 115-row
  `post_merge_gate_failure` category groups into **10 subfamilies (43, 13, 13, 10, 9, 8, 7, 4,
  2, 2) + 4 singletons = 111/115 grouped**, each keyed on a genuinely distinct failure tail.
- @ 0.95 (the card's number): only 77 rows collapse; the target family does NOT unify. **★ 0.7 is
  the default** (see D0). Unifying 115 heterogeneous rows would be the false merge CB-43's RISK
  section calls worse than duplication.
- **Review-corrected honesty about this evidence** (mandatory fix 3 context, corroborated):
  - The "zero cross-file families" probe is **vacuous** — every family found is single-file, so
    it cannot discriminate. The replacement probe: the failure-tail check on each family's
    lowest-scoring pair (defender ran it by hand on the 43-family: all 43 members share ONE
    failure tail — WAL checkpoint — so that family is coherent; but the report must carry the
    evidence, not the assertion).
  - Union-find takes the transitive closure: the 43-family's **minimum pair score is 0.393**
    (Codex, reproduced twice). Families are connected components, and the report must say so and
    report its own chain quality (min_pair_score, edges) — see D4's report design.
  - Category blocking was the measurement method, so "cross-category similarity had zero observed
    value" is circular — the block is a **cost bound**, not an evidenced finding (CX-smell-1).
  - The defender re-ran the measurement through the shipped `normalize_text` (ANSI strip on AND
    off): **identical results — 11 families / 102 collapse**. Reproducibility is now a repo
    obligation: the script ships as `tests/manual/verify_similarity_corpus.py` (Task 6) and the
    tolerance is exact (family and collapse counts match; only row totals may drift).
- Guard rails from measurement: descriptions contain ANSI color remnants (`[0m[32m…`) —
  similarity-side normalization strips them (the fingerprint normalization is versioned `auto:v1`
  and must not change); trigram Jaccard is meaningless on short strings ("Bug 1" vs "Bug 2" ≈
  0.8), so texts under `MIN_TEXT_LEN = 40` normalized chars are excluded **in the scoring layer**
  (resolver, report, and check share the one policy — mandatory fix 3). Fixture reference
  numbers: jaccard(LONG_A, LONG_A2) = 0.96, jaccard(LONG_A, LONG_B) = 0.215.

### D2. The pre-add resolver seam (`db.py`)

The seam CB-44 refused to build speculatively, now designed against its first consumer. The
card's ratified scope item 4 mandates by name: outcome type expresses annotate distinctly from
redirect; resolver failures queryable; per-resolver SAVEPOINT; a resolver must never commit.

- **Registration**: `db.register_pre_add_resolver(name, fn, *, meta_keys)`. Same discipline as
  the other **hook** registries (post-add, status-change): an identical re-registration is a
  silent no-op (module re-import safe) — but a same-name registration with DIFFERENT `meta_keys`
  **raises** (a silently ignored contract change is CB-15's shape). Registration refuses
  `meta_keys` overlapping any previously registered resolver's (5-line insurance against CB-16's
  last-assignment-wins at the seam level; not load-bearing today with one resolver) and refuses
  the runner's own key `resolver_errors`.
- **Resolver contract**: `fn(conn, observation) -> dict[str, Any] | None`. A returned dict is an
  ANNOTATE outcome — a meta patch merged into the new row's meta before INSERT. `None` = no
  opinion. **★ Redirect is deliberately not expressible**: the return type has no channel for it.
  Identity is core (CB-44 ratified); a future redirect variant would be a new typed return
  negotiated with its own first consumer.
- **Observation dict** (read-only context): `finding_id` (the id about to be inserted),
  `severity`, `category`, `file`, `description`, `source`, `tags`, `meta` (caller's, validated),
  `fingerprint`, `dedup_action` (context ONLY — see firing rule), `recurrence_of`, `at` (the
  row's timestamp; the field is named `at` everywhere — prose, code, tests).
- **The transaction contract is ENFORCED, not documented** (mandatory fix 2; corroborated by both
  models and empirically verified — a bare `SAVEPOINT`/`RELEASE` outside a transaction IS a
  commit in sqlite3):
  1. **Entry guard**: `if not conn.in_transaction: raise RuntimeError(...)` — raise, never assert
     (stripped under `-O`), the mirror image of `merge.merge` / `pull_next`'s ambient-transaction
     refusal and for the mirror-image reason: outside a transaction the runner would commit.
  2. **Post-resolver guard**: after EVERY resolver call, `if not conn.in_transaction: raise
     RuntimeError(f"resolver {name!r} closed the caller's transaction")` — deliberately OUTSIDE
     the failure-swallow: a resolver that committed the caller's work is not an annotation
     failure, it is corruption, and swallowing it would let the pending INSERT land outside any
     transaction (CX-major-2, the review's sharpest finding).
  3. **Guarded cleanup**: the `ROLLBACK TO`/`RELEASE` in the except arm is itself wrapped; an
     `OperationalError` there raises a named "resolver corrupted the savepoint stack" error
     `from` the original — never masking it, never converting a swallowed annotation failure
     into a lost finding (SERIOUS-11; `db.txn`'s own cleanup lesson at db.py:299-300).
  4. **Outcome validation INSIDE the savepoint** (SERIOUS-1/CX-7): the outcome must be a dict
     with string keys, its keys ⊆ the resolver's declared `meta_keys` and disjoint from
     `forbidden`, and `json.dumps(outcome, allow_nan=False)` must succeed (validate, discard) —
     so a non-serializable or NaN-carrying patch takes the queryable failure path instead of
     aborting the add at findings.py's later `json.dumps(meta_final)`.
- **Runner**: `db.run_pre_add_resolvers(conn, observation, *, forbidden=frozenset()) -> dict`.
  Per resolver: `SAVEPOINT sp_pre_add_<idx>` (index-named — never an identifier from
  resolver-supplied text; carries the S608-style justification comment), call, validate, RELEASE;
  on failure ROLLBACK TO + RELEASE (guarded, rule 3), one stderr line, and append
  `{"resolver": name, "error": str(e)[:500], "at": observation["at"]}` to the patch under
  `resolver_errors`. `forbidden` exists because db.py must not import findings — the caller
  passes its own reserved keys.
- **`db.resolver_reserved_meta_keys()` calls `_ensure_modules_loaded()` first** (mandatory fix
  7), exactly like `get_tool_providers`/`get_cli_providers` — the reserved set must not depend on
  which modules a process happened to import (SERIOUS-2/CX-6: the same `meta={"similar_to": ...}`
  was accepted on a bare library connection and refused under the server).
- **Failure surfacing (queryable)**: `meta.resolver_errors` on the inserted finding — findable
  via `query(meta_key="resolver_errors")`, no new tables. stderr is the immediate channel; the
  meta stamp is the durable one.
- **Firing rule ★ (mandatory fix 4)**: THE predicate is `finding_id is None` — an explicit id
  asserts identity and bypasses the whole observation machinery (dedup, hooks, and resolvers
  alike; also keeps the 158/173 explicit-id test fixtures annotation-free). `dedup_action` is
  context in the observation, NEVER the predicate (an explicit-id insert also carries
  `"created"`, so a membership test on it fires on the exact case the rule excludes — the
  review caught the plan's own prose making that error). Bumps and reopens do not reach the
  insert path and therefore never fire. **CSV import is a no-id add** (verified at
  findings.py:1650-1659 — an earlier draft claimed the opposite) **and must not fire resolvers**:
  an import is not an observation. `add_finding` gains `annotate: bool = True`; `_cmd_import_csv`
  passes `annotate=False`. This also removes the import leg of the write-lock cost hazard.

### D3. findings.py integration

- **New public accessor — the module-boundary fix** (mandatory fix 1; corroborated FATAL: no
  module today reads the findings table, and similarity.py must not become the first):

  ```python
  LIVE_STATUSES = _LIVE_STATUSES  # public alias

  def similarity_candidates(
      conn, *, category=None, status=None, statuses=None, exclude_id=None, limit=None
  ) -> list[dict[str, Any]]
  ```
  Returns raw rows with keys `id, category, file, status, severity, occurrence_count,
  created_at, description, meta_json` — `meta_json` is the STORED STRING, never parsed (CB-24
  consequence 4: parsing here would make the caller's tolerate-and-degrade policy
  unimplementable). `ORDER BY created_at, id` — deterministic, which is what makes any grouping
  over it deterministic (SERIOUS-5: `utc_now()` is whole-second, ties are common). `status`
  resolves through `is_vocabulary_filter_active` + `resolve_finding_status` INSIDE findings
  (CB-19/CB-25, applied by the owner); `statuses` takes an explicit tuple (e.g. the live set);
  `limit` (with newest-first option via `order="newest"`) is what bounds the resolver's pool.
- New public `normalized_identity_text(description, meta=None)` — thin wrapper over
  `_normalize_for_fingerprint` (similarity must not import privates; algorithm stays
  single-sourced and versioned).
- `_validate_meta_keys(meta, *, updating=False)` refuses `_RESERVED_META_KEYS ∪
  db.resolver_reserved_meta_keys()` on ADD; on UPDATE (`updating=True`) it refuses the same set
  MINUS `{"similar_to"}` (mandatory fix 9 / SERIOUS-10: the add-side reservation stops caller
  spoofing, but a permanently unrepairable annotation is the CB-26 shape — a re-scrub must be
  able to rewrite or clear `similar_to` via `update_finding(meta_update=...)`; `resolver_errors`
  stays refused on both paths).
- `_add_one`: on the insert path, when `finding_id is None and annotate`, build the observation,
  `meta_final.update(db.run_pre_add_resolvers(conn, observation,
  forbidden=_RESERVED_META_KEYS))`, then the one INSERT proceeds unchanged.
- **Batch semantics stated ★ (mandatory fix 10)**: inside `batch_add_findings`' single
  transaction, members 1..k−1 are VISIBLE to member k's resolver (same connection, open
  transaction), so annotation is input-order-dependent and asymmetric — member 3 may point at
  members 1–2, which point at nothing. KEPT deliberately: it matches the identity function's own
  intra-batch behaviour ("the row AS OBSERVED when that member was processed"). Pinned by a test.
- **CSV import strips the dynamic union** (mandatory fix 8 / CX-5): the meta filter at
  findings.py:1637 becomes `k not in (_RESERVED_META_KEYS | db.resolver_reserved_meta_keys())`,
  so an exported `similar_to`/`resolver_errors` does not kill the re-import mid-way. Round-trip
  test seeds an ANNOTATED row.

### D4. similarity.py (extension module)

- **No SQL.** All row access via `findings.similarity_candidates`. Tolerant meta parse lives in
  ONE place over the accessor's raw `meta_json`: invalid JSON **or valid non-dict JSON**
  (`"[1,2]"`, `"3"` — CX-13) degrades to `{}`.
- **Normalization**: `normalize_text(description, meta)` = `findings.normalized_identity_text` +
  ANSI-remnant strip (`\x1b\[[0-9;]*m` and ESC-stripped `\[[0-9;]{1,6}m`) + whitespace collapse.
- **Detector**: char 3-grams (symmetric padding `f" {text} "`), Jaccard. `DEFAULT_THRESHOLD =
  0.7`, `MIN_TEXT_LEN = 40`, `MAX_ANNOTATIONS = 5`, `CANDIDATE_POOL_LIMIT = 500`.
- **Scoring-layer guard** (mandatory fix 3): rows (and queries) whose normalized text is under
  `MIN_TEXT_LEN` are excluded from pairing EVERYWHERE — resolver, report, and check share the
  policy; the report counts exclusions as `rows_skipped_short`. (Without this,
  `jaccard(trigram_set(""), trigram_set(""))` = 1.0 and every short row merges with every other.)
- **Input validation** (mandatory fix 11): every public surface validates `0.0 <= threshold <=
  1.0` and `limit >= 0` (a negative limit silently returns the WORST matches via negative
  slicing); `category` uses `types.is_text_filter_active` (the free-text filter predicate
  `query_findings` already uses for `fingerprint`), not bare `is not None`.
- **Vector escape hatch — offline only ★** (CX-12 resolved by narrowing): `group_report` accepts
  `vectors: dict[finding_id, list[float]] | None`; a pair scores by cosine when BOTH members have
  vectors, else lexically. The file-time surfaces do NOT take vectors — an MCP client cannot
  practically pass thousands of vectors per call; that is the honest reason and it is stated.
  `cosine` raises `ValueError` on mismatched dimensions (`zip` would silently truncate the dot
  product but not the norms — CX-smell-5).
- **File-time resolver** `similarity.annotate` (`meta_keys=("similar_to",)`): candidate pool =
  `similarity_candidates(category=<same>, statuses=LIVE + (wont_fix, not_a_bug),
  limit=CANDIDATE_POOL_LIMIT, order="newest")`. Terminal-decision rows are IN the pool
  (CX-smell-2, the review's best design insight: "resembles CB-N, already dismissed" is the
  single most valuable annotation, and each `similar_to` entry carries the candidate's `status`
  so a reader can tell a decision from an open card). `fixed` stays OUT — exact matches already
  reopen; a near-match to a fixed card is genuinely ambiguous and belongs in the offline scrub.
  Stamp: `meta.similar_to = [{"id", "score", "status"}]`, ≥ threshold, top `MAX_ANNOTATIONS`,
  sorted (-score, id). The pool limit is a documented BOUND, not a guess: a file-time advisory
  needs recent history; completeness lives in the offline scrub.
- **Cost control** (mandatory fix 10): module-level trigram memo keyed `(id, updated_at)`, size
  capped ~2000 entries FIFO — correct because a bumped row changes `updated_at` and re-normalizes.
  Measured: largest live category 92 rows × 1498-char descriptions = 23.7 ms/scan; memoization
  collapses a 100-member batch from ~2.4 s of write-locked scanning to ~one scan + set
  intersections.
- **Offline scrub — auditable evidence, not just membership** (mandatory fix 3, structural):
  `group_report(conn, *, threshold=0.7, category=None, status=None, vectors=None,
  family_limit=None, member_limit=None)` returns:

  ```python
  {
    "threshold": ..., "populations": ["open", "in_progress", "stale"],  # what was included
    "rows_considered": N, "rows_skipped_short": K, "collapse_count": C,
    "families": [{
        "category": ..., "size": ..., "min_pair_score": 0.393,  # chain diameter, visible
        "edge_count": ..., "edges": [{"a": id, "b": id, "score": 0.71}, ...],
        "members": [{"id", "status", "severity", "occurrence_count", "created_at", "file",
                     "description_excerpt"  # first 200 chars — sample-audit needs text
        }, ...],
    }, ...],
    "families_total": ..., "members_total": ...,  # so truncation is visible
  }
  ```
  Default population = the LIVE set (SERIOUS-3: grouping terminal rows into a merge dry run
  contradicts decision-stays-decided); `status=` widens explicitly. Families are connected
  components and the output SAYS so via `min_pair_score` + edges — CB-46 sets its own diameter
  bar without re-deriving the graph. Member sort key `(created_at, id)`; family sort
  `(-size, members[0]["id"])` — deterministic end to end.
- **Surfaces**: MCP `similarity_check(description, category, meta=None, threshold=0.7, limit=5)`
  (applies EXACTLY the resolver's policy — same pool, same `MIN_TEXT_LEN` gate — or its docstring
  is a lie) and `similarity_report(threshold=0.7, category=None, status=None, family_limit=None,
  member_limit=None)`. `meta` typed `dict[str, Any] | None` (the findings `add` tool's own
  convention at findings.py:1113 — the `str | list | None` rule is about JSON-encoded lists).
  CLI `similarity-check` / `similarity-report`, handlers `cmd_similarity_check` /
  `cmd_similarity_report` — FULL bodies in Task 5 (a merge-gating plan reviews its error-arm
  ordering; `similarity_report` parses stored meta, so `json.JSONDecodeError` must re-raise from
  an arm BEFORE `(KeyError, ValueError)`, the `_cmd_update` pattern). Mode slug `similarity` in
  `SERVER_NAMES` + cli allowlist; module in `_ensure_modules_loaded`; no schema. Golden schema
  regenerated. The resolver registers whenever the package loads, so annotation works in every
  `--mode`; mode filtering gates only the tools/CLI.

### D5. Requirements parity — ★ DECIDED: requirements get NO identity function

(Review verdict: NOT a scope reversal — the card's scope item 5 delegates this verbatim:
"decide deliberately whether reqs gets the same treatment or documents why not".) Requirement
rows are authored artifacts with caller-assigned ids (import keyed by FR-id — identity already
asserted on every write path, the same explicit-id bypass that skips dedup for findings); no
automated filer emits requirement observations; similarity for requirements exists via
embeddings. A fingerprint column with zero writers would be dead code. Recorded in CLAUDE.md
beside the findings-identity entry. Revisit trigger: an automated requirements filer appearing.

### D6. CB-46 merge policy — PROPOSED, explicitly NOT ratified (stays on CB-46)

The scrub report (now with edges, min_pair_score, and description excerpts — the sample-audit
evidence CB-46's card demands) is CB-46's dry run. Proposed policy to be ratified with the user
before any backfill write: survivor = oldest row of a family (deterministic NOW that the accessor
orders by `created_at, id`); losers become terminal with `meta.merged_into = <survivor>`. Open
questions that block execution: which terminal status merged-away rows get (`wont_fix` is
semantically wrong; a new `duplicate` status touches branch-totality and CHECK constraints);
dangling external references — commit trailers, milestone items, claims, **and blocker links**
(CX-missing-8); ring capacity (a 43-row family exceeds the 20-entry ring). This plan ships none
of it.

---

## File Structure

- Modify `src/codebugs/db.py` — pre-add resolver registry + runner (§D2), beside the existing
  hook registries; `_ensure_modules_loaded` gains `similarity`.
- Modify `src/codebugs/findings.py` — `similarity_candidates`, `LIVE_STATUSES`,
  `normalized_identity_text`, reserved-key union with update carve-out, `annotate` flag, seam
  call in `_add_one`, CSV-import strip fix (§D3).
- Create `src/codebugs/similarity.py` — detector, resolver, report, MCP tools, CLI (§D4).
- Modify `src/codebugs/server.py` (`SERVER_NAMES`), `src/codebugs/cli.py` (`--mode` allowlist).
- Create `tests/test_pre_add_seam.py` — seam contract + hostile-resolver + findings-integration
  tests.
- Create `tests/test_similarity.py` — detector, resolver, report, registration-and-CALL tests.
- Create `tests/manual/verify_similarity_corpus.py` — the reproducible calibration script.
- Regenerate `tests/golden/mcp_schema.json`.
- Update `CLAUDE.md`, ledger, handoff; close CB-45; annotate CB-46.

Test-registry hygiene: pytest collection imports `test_similarity.py`, which registers the REAL
resolver process-wide. `tests/test_pre_add_seam.py`'s autouse fixture therefore sets
`db._pre_add_resolvers` to a snapshot EXCLUDING `similarity.annotate` by name — order-independent
by construction, and the docstring says why (WEAKNESS-8).

---

### Task 1: Pre-add resolver seam in db.py (with the enforced transaction contract)

**Files:**
- Modify: `src/codebugs/db.py` (after the status-change hook registry, ~line 250)
- Test: `tests/test_pre_add_seam.py` (create)

**Interfaces:**
- Produces: `db.register_pre_add_resolver(name: str, fn, *, meta_keys: tuple[str, ...]) -> None`;
  `db.resolver_reserved_meta_keys() -> frozenset[str]`;
  `db.run_pre_add_resolvers(conn, observation: dict, *, forbidden: frozenset = frozenset()) -> dict`;
  module-level `db._pre_add_resolvers` list (tests snapshot it).

- [ ] **Step 1: Write the failing tests**

```python
"""Pre-add resolver seam (CB-45): registration, SAVEPOINT isolation, the enforced
never-commit contract, hostile resolvers, and failure surfacing."""

import sqlite3

import pytest

from codebugs import db, findings


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Snapshot the registry EXCLUDING similarity.annotate: pytest collection imports
    test_similarity.py first, which registers the real resolver process-wide; excluding
    it by name makes these tests order-independent by construction (review W-8)."""
    snapshot = [r for r in db._pre_add_resolvers if r.name != "similarity.annotate"]
    monkeypatch.setattr(db, "_pre_add_resolvers", snapshot)


def _obs(**over):
    base = {
        "finding_id": "CB-999", "severity": "low", "category": "c", "file": "f",
        "description": "d", "source": "test", "tags": [], "meta": {},
        "fingerprint": "fp-1", "dedup_action": "created", "recurrence_of": None,
        "at": "2026-08-16T00:00:00Z",
    }
    base.update(over)
    return base


INSERT_ROW = ("INSERT INTO findings (id, severity, category, file, status, description,"
              " source, tags, meta, created_at, updated_at) VALUES (?,'low','x','x','open',"
              "'x','t','[]','{}','2026-01-01','2026-01-01')")


class _Abort(Exception):
    pass


class TestRegistration:
    def test_identical_reregistration_is_noop(self):
        n = len(db._pre_add_resolvers)
        fn = lambda c, o: None  # noqa: E731
        db.register_pre_add_resolver("t.a", fn, meta_keys=("ka",))
        db.register_pre_add_resolver("t.a", fn, meta_keys=("ka",))
        assert len(db._pre_add_resolvers) == n + 1

    def test_same_name_different_meta_keys_raises(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="meta_keys"):
            db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("kz",))

    def test_overlapping_meta_keys_refused(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="ka"):
            db.register_pre_add_resolver("t.b", lambda c, o: None, meta_keys=("ka", "kb"))

    def test_resolver_errors_key_refused(self):
        with pytest.raises(ValueError, match="resolver_errors"):
            db.register_pre_add_resolver("t.a", lambda c, o: None,
                                         meta_keys=("resolver_errors",))

    def test_reserved_union(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka", "kb"))
        assert {"ka", "kb", "resolver_errors"} <= db.resolver_reserved_meta_keys()


class TestTransactionContract:
    """The card's strongest ratified requirement: a resolver must never commit."""

    def test_runner_outside_transaction_raises(self, conn):
        # Outside a txn, SAVEPOINT starts one and RELEASE COMMITS it — the runner
        # must refuse (raise, never assert) rather than silently commit.
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        assert not conn.in_transaction
        with pytest.raises(RuntimeError, match="OPEN transaction"):
            db.run_pre_add_resolvers(conn, _obs())

    def test_runner_leaves_caller_transaction_open(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        with pytest.raises(_Abort):
            with db.txn(conn):
                conn.execute(INSERT_ROW, ("CB-777",))
                db.run_pre_add_resolvers(conn, _obs())
                assert conn.in_transaction  # still inside the caller's txn
                raise _Abort()  # db.txn rolls back
        assert conn.execute("SELECT id FROM findings WHERE id='CB-777'").fetchone() is None

    def test_resolver_that_commits_is_unrecoverable_and_loud(self, conn):
        def hostile(c, o):
            c.commit()
            return {"ka": 1}

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError, match="closed the caller's transaction"):
                db.run_pre_add_resolvers(conn, _obs())
            # re-open so db.txn's closing COMMIT has a transaction to commit
            conn.execute("BEGIN IMMEDIATE")

    def test_resolver_that_rolls_back_is_unrecoverable_and_loud(self, conn):
        def hostile(c, o):
            c.execute("ROLLBACK")
            return {"ka": 1}

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError):
                db.run_pre_add_resolvers(conn, _obs())
            conn.execute("BEGIN IMMEDIATE")

    def test_failing_resolver_that_destroyed_savepoint_raises_named_error(self, conn):
        def hostile(c, o):
            c.execute("ROLLBACK")  # destroys txn AND savepoint
            raise RuntimeError("boom")

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError, match="savepoint stack"):
                db.run_pre_add_resolvers(conn, _obs())
            conn.execute("BEGIN IMMEDIATE")


class TestRunner:
    def test_annotation_merged(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": [o["finding_id"]]},
                                     meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch == {"ka": ["CB-999"]}

    def test_none_means_no_opinion(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with db.txn(conn):
            assert db.run_pre_add_resolvers(conn, _obs()) == {}

    def test_failure_rolls_back_resolver_writes_only(self, conn):
        def bad(c, o):
            c.execute(INSERT_ROW, ("CB-666",))
            raise RuntimeError("boom")

        db.register_pre_add_resolver("t.bad", bad, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
            assert conn.in_transaction
            row = conn.execute("SELECT id FROM findings WHERE id='CB-666'").fetchone()
        assert row is None
        assert patch["resolver_errors"][0]["resolver"] == "t.bad"
        assert "boom" in patch["resolver_errors"][0]["error"]
        assert patch["resolver_errors"][0]["at"] == "2026-08-16T00:00:00Z"

    def test_failure_does_not_stop_later_resolvers(self, conn):
        db.register_pre_add_resolver("t.bad", lambda c, o: 1 / 0, meta_keys=("ka",))
        db.register_pre_add_resolver("t.ok", lambda c, o: {"kb": 1}, meta_keys=("kb",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch["kb"] == 1 and len(patch["resolver_errors"]) == 1

    def test_undeclared_key_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"other": 1}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "other" not in patch and patch["resolver_errors"][0]["resolver"] == "t.a"

    def test_forbidden_key_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs(), forbidden=frozenset({"ka"}))
        assert "ka" not in patch and patch["resolver_errors"]

    def test_non_dict_outcome_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: ["ka"], meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch["resolver_errors"][0]["resolver"] == "t.a"

    def test_non_serializable_outcome_is_failure_not_crash(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": {1, 2}}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "ka" not in patch and patch["resolver_errors"]

    def test_nan_outcome_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": float("nan")},
                                     meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "ka" not in patch and patch["resolver_errors"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_pre_add_seam.py -v`
Expected: FAIL/ERROR with `AttributeError: module 'codebugs.db' has no attribute
'register_pre_add_resolver'`.

- [ ] **Step 3: Implement the seam in db.py**

Insert after `run_status_change_hooks`:

```python
# --- Pre-add resolver seam (CB-45) ---


@dataclass(frozen=True)
class PreAddResolver:
    """A registered pre-add resolver: annotates a new finding before its INSERT.

    ANNOTATE-ONLY by construction: the resolver returns a meta patch (or None);
    there is no redirect channel — identity routing is core (CB-44, ratified).
    """

    name: str
    fn: Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any] | None]
    meta_keys: frozenset[str]


_pre_add_resolvers: list[PreAddResolver] = []

_RESOLVER_ERRORS_KEY = "resolver_errors"


def register_pre_add_resolver(
    name: str,
    fn: Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any] | None],
    *,
    meta_keys: tuple[str, ...],
) -> None:
    """Register a pre-add resolver.

    Same discipline as the other HOOK registries (post-add, status-change): an
    identical re-registration is a silent no-op so module re-import is safe —
    but a same-name registration with DIFFERENT meta_keys raises, because a
    silently ignored contract change is CB-15's failure shape. `meta_keys`
    declares the ONLY meta keys this resolver's annotation may write; findings
    reserves the union against caller-supplied meta. Overlap with another
    resolver's keys is refused (CB-16's last-assignment-wins, at the seam level).
    """
    keys = frozenset(meta_keys)
    for existing in _pre_add_resolvers:
        if existing.name == name:
            if existing.meta_keys != keys:
                raise ValueError(
                    f"resolver {name!r} re-registered with different meta_keys "
                    f"{sorted(keys)} (was {sorted(existing.meta_keys)})"
                )
            return
    if _RESOLVER_ERRORS_KEY in keys:
        raise ValueError(f"meta key {_RESOLVER_ERRORS_KEY!r} is reserved for the runner")
    for existing in _pre_add_resolvers:
        overlap = keys & existing.meta_keys
        if overlap:
            raise ValueError(
                f"meta keys {sorted(overlap)} already declared by resolver {existing.name!r}"
            )
    _pre_add_resolvers.append(PreAddResolver(name, fn, keys))


def resolver_reserved_meta_keys() -> frozenset[str]:
    """Every meta key any registered resolver may write, plus the runner's own.

    Loads the domain modules first (same as get_tool_providers/get_cli_providers):
    the reserved set must not depend on which modules a process happened to
    import — the same meta would be accepted on a bare library connection and
    refused under the server (CB-45 review, corroborated).
    """
    _ensure_modules_loaded()
    keys = {_RESOLVER_ERRORS_KEY}
    for r in _pre_add_resolvers:
        keys |= r.meta_keys
    return frozenset(keys)


def _validate_resolver_outcome(
    outcome: dict[str, Any], resolver: PreAddResolver, forbidden: frozenset[str]
) -> None:
    """Raise unless the outcome is a JSON-serializable dict within declared keys.

    Runs INSIDE the resolver's savepoint/try so a bad outcome takes the queryable
    failure path — otherwise the later json.dumps(meta_final) in findings would
    abort the whole add with no resolver_errors stamp (review SERIOUS-1).
    """
    if not isinstance(outcome, dict) or any(not isinstance(k, str) for k in outcome):
        raise ValueError("resolver outcome must be a dict with string keys")
    bad = (set(outcome) - resolver.meta_keys) | (set(outcome) & forbidden)
    if bad:
        raise ValueError(f"resolver wrote undeclared/forbidden meta keys {sorted(bad)}")
    json.dumps(outcome, allow_nan=False)  # validate serializability; discard


def run_pre_add_resolvers(
    conn: sqlite3.Connection,
    observation: dict[str, Any],
    *,
    forbidden: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run every resolver against one observation; return the merged meta patch.

    NEVER-COMMIT is enforced, not documented: (1) outside an open transaction a
    bare SAVEPOINT/RELEASE would BE a commit, so the runner refuses up front —
    raise, never assert, mirroring merge.merge's ambient-transaction refusal in
    the opposite direction; (2) a resolver that closes the caller's transaction
    (commit()/ROLLBACK through the raw connection) is corruption, not an
    annotation failure — detected after every call, OUTSIDE the swallow;
    (3) cleanup is guarded so it never masks the real exception and never
    converts a swallowed annotation failure into a lost finding (db.txn's own
    cleanup lesson). Each resolver runs in SAVEPOINT sp_pre_add_<idx> —
    index-named, never an identifier built from resolver-supplied text (the
    interpolated-identifier discipline). Failures are stamped QUERYABLY into
    the patch under `resolver_errors` (query(meta_key="resolver_errors")).
    `forbidden` carries the caller's own reserved keys, because db must not
    import findings.
    """
    if not conn.in_transaction:
        raise RuntimeError(
            "run_pre_add_resolvers() requires an OPEN transaction: outside one, "
            "SAVEPOINT opens a transaction and RELEASE COMMITS it — the runner "
            "would commit the resolver's writes, the inverse of the never-commits "
            "contract"
        )
    patch: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for idx, resolver in enumerate(_pre_add_resolvers):
        sp = f"sp_pre_add_{idx}"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            outcome = resolver.fn(conn, observation)
            if not conn.in_transaction:
                raise _ResolverBrokeTransaction(resolver.name)
            if outcome is not None:
                _validate_resolver_outcome(outcome, resolver, forbidden)
                patch.update(outcome)
            conn.execute(f"RELEASE {sp}")
        except _ResolverBrokeTransaction:
            raise RuntimeError(
                f"pre-add resolver {resolver.name!r} closed the caller's transaction; "
                f"the pending INSERT would land outside any transaction"
            ) from None
        except Exception as e:  # noqa: BLE001
            try:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
            except sqlite3.OperationalError:
                raise RuntimeError(
                    f"pre-add resolver {resolver.name!r} corrupted the savepoint stack"
                ) from e
            sys.stderr.write(f"[pre-add resolver '{resolver.name}' failed] {e}\n")
            errors.append(
                {"resolver": resolver.name, "error": str(e)[:500],
                 "at": observation.get("at")}
            )
    if errors:
        patch[_RESOLVER_ERRORS_KEY] = errors
    return patch


class _ResolverBrokeTransaction(Exception):
    """Internal sentinel: a resolver closed the caller's transaction."""
```

(Place `_ResolverBrokeTransaction` above the runner in the actual file. `dataclass`, `Callable`,
`Any`, `sys`, `json`, `sqlite3` — verify imports, add missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_pre_add_seam.py -v`
Expected: all PASS. NOTE on the hostile-commit tests: after the runner raises, the caller's
transaction is gone; `db.txn`'s closing COMMIT would raise — hence the tests re-open with
`BEGIN IMMEDIATE` before the `with` block exits. If `db.txn`'s COMMIT still errors, adjust the
tests to catch at the `with` boundary rather than weakening the runner.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/db.py tests/test_pre_add_seam.py
git commit -m "feat(seam): pre-add resolver registry with enforced never-commit contract (CB-45)"
```

### Task 2: findings.py — accessor, reserved-key union, seam call, annotate flag, CSV strip

**Files:**
- Modify: `src/codebugs/findings.py`
- Test: `tests/test_pre_add_seam.py` (extend)

**Interfaces:**
- Consumes: Task 1's runner + `resolver_reserved_meta_keys`.
- Produces: `findings.LIVE_STATUSES: tuple[str, ...]`;
  `findings.normalized_identity_text(description, meta=None) -> str`;
  `findings.similarity_candidates(conn, *, category=None, status=None, statuses=None,
  exclude_id=None, limit=None, order="oldest") -> list[dict]` (keys: id, category, file, status,
  severity, occurrence_count, created_at, description, meta_json — meta_json is the RAW string);
  `add_finding(..., annotate: bool = True)`; resolver annotations landing in add/batch results.

- [ ] **Step 1: Write the failing tests (append to tests/test_pre_add_seam.py)**

```python
LONG_DESC = "a genuinely long description of a defect that clears the guard " * 2


class TestFindingsIntegration:
    def test_annotation_lands_in_inserted_row(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": o["category"]},
                                     meta_keys=("ka",))
        result = findings.add_finding(conn, severity="low", category="cat-x", file="f",
                                      description=LONG_DESC)
        assert result["meta"]["ka"] == "cat-x"
        stored = findings.get_finding(conn, result["id"])
        assert stored["meta"]["ka"] == "cat-x"  # in the INSERT, not a later UPDATE

    def test_bump_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1),
                                     meta_keys=("ka",))
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        findings.add_finding(conn, **kw)
        second = findings.add_finding(conn, **kw)
        assert second["dedup_action"] == "bumped" and len(calls) == 1

    def test_reopen_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1) or None,
                                     meta_keys=("ka",))
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        first = findings.add_finding(conn, **kw)
        findings.update_finding(conn, first["id"], status="fixed")
        again = findings.add_finding(conn, **kw)
        assert again["dedup_action"] == "reopened" and len(calls) == 1

    def test_recurrence_insert_fires_resolvers(self, conn):
        seen = []
        db.register_pre_add_resolver(
            "t.a", lambda c, o: seen.append(o["dedup_action"]) or None, meta_keys=("ka",))
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        first = findings.add_finding(conn, **kw)
        findings.update_finding(conn, first["id"], status="wont_fix")
        again = findings.add_finding(conn, **kw)
        assert again["dedup_action"] == "recurrence_of_closed"
        assert seen == ["created", "recurrence_of_closed"]

    def test_explicit_id_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1),
                                     meta_keys=("ka",))
        findings.add_finding(conn, severity="low", category="c", file="f",
                             description=LONG_DESC, finding_id="CB-500")
        assert calls == []

    def test_annotate_false_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1),
                                     meta_keys=("ka",))
        findings.add_finding(conn, severity="low", category="c", file="f",
                             description=LONG_DESC, annotate=False)
        assert calls == []

    def test_batch_members_see_earlier_members(self, conn):
        # Pinned semantics (review, mandatory fix 10): inside batch_add's single
        # transaction, member k's resolver sees members 1..k-1 — input-order-
        # dependent and asymmetric, matching identity's own intra-batch behaviour.
        seen_counts = []

        def counting(c, o):
            n = c.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
            seen_counts.append(n)
            return None

        db.register_pre_add_resolver("t.count", counting, meta_keys=("ka",))
        findings.batch_add_findings(conn, [
            {"severity": "low", "category": "c", "file": "f", "description": LONG_DESC + "1"},
            {"severity": "low", "category": "c", "file": "f", "description": LONG_DESC + "2"},
        ])
        assert seen_counts == [0, 1]

    def test_caller_meta_with_resolver_key_refused(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="ka"):
            findings.add_finding(conn, severity="low", category="c", file="f",
                                 description="d", meta={"ka": "spoof"})

    def test_resolver_errors_refused_on_update(self, conn):
        r = findings.add_finding(conn, severity="low", category="c", file="f",
                                 description="d", finding_id="CB-501")
        with pytest.raises(ValueError, match="resolver_errors"):
            findings.update_finding(conn, r["id"], meta_update={"resolver_errors": []})

    def test_failed_resolver_finding_still_lands_with_error_stamp(self, conn):
        db.register_pre_add_resolver("t.bad", lambda c, o: 1 / 0, meta_keys=("ka",))
        result = findings.add_finding(conn, severity="low", category="c", file="f",
                                      description=LONG_DESC)
        assert result["was_new"] is True
        assert result["meta"]["resolver_errors"][0]["resolver"] == "t.bad"

    def test_normalized_identity_text_delegates(self):
        # Pins delegation only — the wrapper exists so similarity never imports
        # the private, versioned normalization.
        assert findings.normalized_identity_text("A  B") == \
            findings._normalize_for_fingerprint("A  B", None)

    def test_live_statuses_public_alias(self):
        assert findings.LIVE_STATUSES == ("open", "in_progress", "stale")


class TestSimilarityCandidates:
    def _seed(self, conn):
        for i, (cat, status) in enumerate(
            [("a", None), ("a", "fixed"), ("b", None), ("a", "wont_fix")]
        ):
            findings.add_finding(conn, severity="low", category=cat, file="f",
                                 description=f"row {i} " * 20, finding_id=f"CB-{i+1}")
            if status:
                findings.update_finding(conn, f"CB-{i+1}", status=status)

    def test_raw_meta_and_ordering(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn)
        assert [r["id"] for r in rows] == ["CB-1", "CB-2", "CB-3", "CB-4"]  # created_at, id
        assert isinstance(rows[0]["meta_json"], str)  # RAW string, never parsed

    def test_category_and_statuses_filters(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(
            conn, category="a", statuses=findings.LIVE_STATUSES + ("wont_fix",))
        assert [r["id"] for r in rows] == ["CB-1", "CB-4"]

    def test_status_vocabulary_resolved(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, status="FIXED")  # CB-19: spelling forgiven
        assert [r["id"] for r in rows] == ["CB-2"]
        with pytest.raises(ValueError):
            findings.similarity_candidates(conn, status="nonsense")

    def test_limit_newest_first(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, limit=2, order="newest")
        assert [r["id"] for r in rows] == ["CB-4", "CB-3"]

    def test_exclude_id(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, category="b", exclude_id="CB-3")
        assert rows == []
```

- [ ] **Step 2: Run to verify the new classes fail**

Run: `uv run python -m pytest tests/test_pre_add_seam.py -v -k "Findings or Candidates"`
Expected: FAIL (missing attributes; annotations absent; spoof not refused).

- [ ] **Step 3: Implement in findings.py**

Public alias + wrapper, beside their private originals:

```python
LIVE_STATUSES = _LIVE_STATUSES  # public: the statuses the partial unique index covers


def normalized_identity_text(description: str, meta: dict[str, Any] | None = None) -> str:
    """Public wrapper over the fallback-fingerprint normalization (CB-43).

    The similarity extension scores over THIS text so grouping and identity
    agree on what is invariant; the algorithm stays private and versioned.
    """
    return _normalize_for_fingerprint(description, meta)
```

The accessor (near `query_findings`):

```python
def similarity_candidates(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    status: str | None = None,
    statuses: tuple[str, ...] | None = None,
    exclude_id: str | None = None,
    limit: int | None = None,
    order: str = "oldest",
) -> list[dict[str, Any]]:
    """Candidate records for an out-of-domain grouping pass (CB-45).

    The sanctioned read surface for similarity.py — no other module may SELECT
    from findings (module-ownership rule). Returns raw rows: ``meta_json`` is
    the STORED STRING, never parsed here — parsing would raise on legacy rows
    and make the caller's tolerate-and-degrade policy unimplementable (CB-24
    consequence 4). Ordered ``created_at, id`` (``order="newest"`` reverses) so
    any grouping over the result is deterministic despite whole-second
    timestamps. ``status`` is a vocabulary filter (resolved, CB-19/CB-25);
    ``statuses`` is an explicit tuple for callers that know their population.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if is_text_filter_active(category):
        conditions.append("category = ?")
        params.append(category)
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    if statuses:
        conditions.append(f"status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    if exclude_id is not None:
        conditions.append("id != ?")
        params.append(exclude_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    direction = "DESC" if order == "newest" else "ASC"
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT id, category, file, status, severity, occurrence_count, created_at, "
        f"description, meta AS meta_json FROM findings {where} "
        f"ORDER BY created_at {direction}, id {direction} {limit_sql}",  # noqa: S608
        params,
    ).fetchall()
    return [dict(r) for r in rows]
```

(`is_text_filter_active` is already imported for the fingerprint filter; verify.)

`_validate_meta_keys` — reserved union with the update carve-out (SERIOUS-10):

```python
def _validate_meta_keys(meta: dict[str, Any] | None, *, updating: bool = False) -> None:
    """Refuse caller meta colliding with identity-machinery or resolver keys.

    On UPDATE, `similar_to` is deliberately writable: the add-side reservation
    stops spoofing, but a permanently unrepairable annotation is the CB-26
    shape — a re-scrub must be able to rewrite or clear it. `resolver_errors`
    stays refused on both paths.
    """
    if not meta:
        return
    reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
    if updating:
        reserved -= {"similar_to"}
    hit = reserved & set(meta)
    if hit:
        raise ValueError(f"meta keys {sorted(hit)} are reserved for the identity machinery")
```

Update `update_finding`'s call site to `_validate_meta_keys(meta_update, updating=True)`.

`add_finding` gains `annotate: bool = True` (docstring: "annotate=False skips pre-add resolvers;
used by CSV import — an import is not an observation"); `batch_add_findings` members do NOT get
the key (imports go through `add_finding`; batch members are real observations).

`_add_one` gains the `annotate` parameter and, on the insert path:

```python
    fid = finding_id or _next_id(conn)
    meta_final = dict(meta or {})
    if recurrence_of is not None:
        meta_final["recurrence_of"] = recurrence_of
    if finding_id is None and annotate:
        # Pre-add resolvers (CB-45): annotate-only. THE predicate is
        # `finding_id is None` — an explicit id asserts identity and bypasses
        # the observation machinery (dedup, hooks, resolvers alike). NOT a
        # dedup_action test: explicit-id inserts also carry "created".
        meta_final.update(
            db.run_pre_add_resolvers(
                conn,
                {
                    "finding_id": fid, "severity": severity, "category": category,
                    "file": file, "description": description, "source": source,
                    "tags": list(tags or []), "meta": dict(meta or {}),
                    "fingerprint": fingerprint, "dedup_action": dedup_action,
                    "recurrence_of": recurrence_of, "at": now,
                },
                forbidden=_RESERVED_META_KEYS,
            )
        )
```

CSV import (`_cmd_import_csv`): pass `annotate=False` in the `add_finding` call, and widen the
stored-meta strip at ~findings.py:1637 to
`k not in (_RESERVED_META_KEYS | db.resolver_reserved_meta_keys())`.

- [ ] **Step 4: Run the seam tests AND the full suite**

Run: `uv run python -m pytest tests/test_pre_add_seam.py -v`, then
`uv run python -m pytest tests/ -q`
Expected: PASS. No similarity resolver exists yet, so any pre-existing breakage is a seam bug —
investigate before touching any test.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/findings.py tests/test_pre_add_seam.py
git commit -m "feat(findings): similarity_candidates accessor + pre-add seam wiring (CB-45)"
```

### Task 3: similarity.py — detector and grouping core (zero SQL)

**Files:**
- Create: `src/codebugs/similarity.py`
- Test: `tests/test_similarity.py` (create)

**Interfaces:**
- Consumes: `findings.similarity_candidates`, `findings.normalized_identity_text`,
  `findings.LIVE_STATUSES` (Task 2).
- Produces: `similarity.normalize_text(description, meta=None) -> str`;
  `similarity.trigram_set(text) -> frozenset[str]`; `similarity.jaccard(a, b) -> float`;
  `similarity.cosine(a, b) -> float` (raises on dim mismatch);
  `similarity.find_similar(conn, *, description, category, meta=None, threshold=0.7,
  limit=5) -> list[{"id","score","status"}]`;
  `similarity.group_report(conn, *, threshold=0.7, category=None, status=None, vectors=None,
  family_limit=None, member_limit=None) -> dict` (shape per D4);
  constants `DEFAULT_THRESHOLD = 0.7`, `MIN_TEXT_LEN = 40`, `MAX_ANNOTATIONS = 5`,
  `CANDIDATE_POOL_LIMIT = 500`.

- [ ] **Step 1: Write the failing tests**

```python
"""Similarity extension (CB-45): lexical detector, auditable grouping report,
file-time annotation."""

import sqlite3

import pytest

from codebugs import db, findings, similarity


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


LONG_A = ("post-merge gate failed on main. log tail: wal checkpoint failed during close:"
          " unable to open database file, worker shutdown aborted")
LONG_A2 = ("post-merge gate failed on main. log tail: wal checkpoint failed during close:"
           " unable to open database file, worker shutdown was aborted")
LONG_B = ("post-merge gate inconclusive on main. log tail: ack = backend.get_ack(timeout="
          "min(liveness_poll_interval, remaining)) timed out waiting for worker")


class TestDetector:
    def test_normalize_strips_ansi_remnants(self):
        assert "[32m" not in similarity.normalize_text("ok [0m[32m.[0m done", None)
        assert "[32m" not in similarity.normalize_text("ok \x1b[32mgreen\x1b[0m done", None)

    def test_jaccard_identity_and_bounds(self):
        t = similarity.trigram_set("some normalized description text")
        assert similarity.jaccard(t, t) == 1.0
        assert similarity.jaccard(t, frozenset()) == 0.0

    def test_near_duplicates_score_high(self):
        a = similarity.trigram_set(similarity.normalize_text(LONG_A, None))
        b = similarity.trigram_set(similarity.normalize_text(LONG_A2, None))
        assert similarity.jaccard(a, b) == pytest.approx(0.96, abs=0.02)

    def test_distinct_defects_score_low(self):
        a = similarity.trigram_set(similarity.normalize_text(LONG_A, None))
        b = similarity.trigram_set(similarity.normalize_text(LONG_B, None))
        assert similarity.jaccard(a, b) == pytest.approx(0.215, abs=0.05)
        assert similarity.jaccard(a, b) < similarity.DEFAULT_THRESHOLD

    def test_cosine(self):
        assert similarity.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert similarity.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert similarity.cosine([0.0], [0.0]) == 0.0

    def test_cosine_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimension"):
            similarity.cosine([1.0, 0.0], [1.0])

    def test_threshold_and_limit_validated(self, conn):
        with pytest.raises(ValueError):
            similarity.find_similar(conn, description=LONG_A, category="g", threshold=1.5)
        with pytest.raises(ValueError):
            similarity.find_similar(conn, description=LONG_A, category="g", limit=-1)
        with pytest.raises(ValueError):
            similarity.group_report(conn, threshold=-0.1)


class TestFindSimilar:
    def _add(self, conn, fid, desc, category="gate", status=None):
        findings.add_finding(conn, severity="low", category=category, file="f",
                             description=desc, finding_id=fid)
        if status:
            findings.update_finding(conn, fid, status=status)

    def test_finds_live_same_category_candidates(self, conn):
        self._add(conn, "CB-1", LONG_A)
        self._add(conn, "CB-2", LONG_B)
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]
        assert out[0]["status"] == "open"
        assert 0.0 < out[0]["score"] <= 1.0

    def test_other_category_excluded(self, conn):
        self._add(conn, "CB-1", LONG_A, category="other")
        assert similarity.find_similar(conn, description=LONG_A2, category="gate") == []

    def test_decided_rows_included_with_status(self, conn):
        # Review CX-smell-2: "resembles CB-N, already dismissed" is the most
        # valuable annotation; wont_fix/not_a_bug are IN the pool, fixed is not.
        self._add(conn, "CB-1", LONG_A, status="wont_fix")
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"] and out[0]["status"] == "wont_fix"

    def test_fixed_rows_excluded(self, conn):
        self._add(conn, "CB-1", LONG_A, status="fixed")
        assert similarity.find_similar(conn, description=LONG_A2, category="gate") == []

    def test_short_query_returns_nothing(self, conn):
        self._add(conn, "CB-1", "Bug 1x")
        assert similarity.find_similar(conn, description="Bug 2x", category="gate") == []

    def test_short_candidates_excluded(self, conn):
        self._add(conn, "CB-1", "Bug 1x")
        assert similarity.find_similar(conn, description=LONG_A, category="gate") == []

    def test_malformed_candidate_meta_tolerated(self, conn):
        self._add(conn, "CB-1", LONG_A)
        conn.execute("UPDATE findings SET meta='{not json' WHERE id='CB-1'")
        conn.commit()
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]

    def test_valid_non_dict_meta_tolerated(self, conn):
        # Review CX-13: valid JSON that is not an object must degrade, not crash.
        self._add(conn, "CB-1", LONG_A)
        conn.execute("UPDATE findings SET meta='[1,2]' WHERE id='CB-1'")
        conn.commit()
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]


class TestGroupReport:
    def _seed3(self, conn):
        for i, d in enumerate([LONG_A, LONG_A2, LONG_B]):
            findings.add_finding(conn, severity="low", category="gate", file="f",
                                 description=d, finding_id=f"CB-{i+1}")

    def test_groups_family_with_audit_evidence(self, conn):
        self._seed3(conn)
        report = similarity.group_report(conn)
        assert report["rows_considered"] == 3
        assert report["rows_skipped_short"] == 0
        assert report["collapse_count"] == 1
        assert report["populations"] == list(findings.LIVE_STATUSES)
        [fam] = report["families"]
        assert {m["id"] for m in fam["members"]} == {"CB-1", "CB-2"}
        assert fam["min_pair_score"] == pytest.approx(0.96, abs=0.02)
        assert fam["edges"] == [
            {"a": "CB-1", "b": "CB-2", "score": pytest.approx(0.96, abs=0.02)}
        ]
        assert "wal checkpoint" in fam["members"][0]["description_excerpt"]

    def test_threshold_respected(self, conn):
        self._seed3(conn)
        strict = similarity.group_report(conn, threshold=0.999)
        assert strict["families"] == [] and strict["collapse_count"] == 0

    def test_default_population_is_live_only(self, conn):
        self._seed3(conn)
        findings.update_finding(conn, "CB-1", status="fixed")
        report = similarity.group_report(conn)
        assert report["rows_considered"] == 2 and report["families"] == []
        widened = similarity.group_report(conn, status="fixed")
        assert widened["rows_considered"] == 1
        assert widened["populations"] == ["fixed"]

    def test_short_rows_skipped_and_counted(self, conn):
        for i, d in enumerate(["Bug 1x", "Bug 2x"]):
            findings.add_finding(conn, severity="low", category="gate", file="f",
                                 description=d, finding_id=f"CB-{i+1}")
        report = similarity.group_report(conn)
        assert report["families"] == [] and report["rows_skipped_short"] == 2

    def test_category_blocking(self, conn):
        findings.add_finding(conn, severity="low", category="a", file="f",
                             description=LONG_A, finding_id="CB-1")
        findings.add_finding(conn, severity="low", category="b", file="f",
                             description=LONG_A, finding_id="CB-2")
        assert similarity.group_report(conn)["families"] == []

    def test_family_and_member_limits_with_visible_totals(self, conn):
        self._seed3(conn)
        report = similarity.group_report(conn, family_limit=0)
        assert report["families"] == [] and report["families_total"] == 1

    def test_vectors_override_pairs_that_have_them(self, conn):
        findings.add_finding(conn, severity="low", category="a", file="f",
                             description=LONG_A, finding_id="CB-1")
        findings.add_finding(conn, severity="low", category="a", file="f",
                             description=LONG_B, finding_id="CB-2")
        vecs = {"CB-1": [1.0, 0.0], "CB-2": [0.96, 0.28]}  # cosine ≈ 0.96
        report = similarity.group_report(conn, vectors=vecs, threshold=0.9)
        assert report["collapse_count"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_similarity.py -v`
Expected: FAIL with `ImportError: cannot import name 'similarity'`.

- [ ] **Step 3: Implement the module core**

```python
"""Similarity extension (CB-45): annotate-only grouping over the identity substrate.

Lexical char-trigram Jaccard over the SAME normalized text the fallback
fingerprint hashes, so grouping and identity agree on what is invariant.
Deterministic, stdlib-only. Caller-supplied vectors slot in for the OFFLINE
path only (embeddings.py precedents exactly this: vectors come from the
caller, codebugs stays model-free) — file-time surfaces take no vectors
because an MCP client cannot practically pass thousands per call.

This is the package's first self-registering non-domain module; what keeps it
legal is that it issues ZERO SQL — all row access goes through the public
findings.similarity_candidates accessor (module-ownership rule).

ANNOTATE, NEVER AUTO-MERGE: nothing here changes identity routing, statuses,
or ids. meta.similar_to is an at-file-time advisory snapshot; readers resolve
referenced ids' CURRENT status, and a re-scrub may rewrite the annotation via
update_finding (the reservation is add-side only).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from typing import Any

from codebugs import db
from codebugs.findings import LIVE_STATUSES, normalized_identity_text, similarity_candidates
from codebugs.types import is_text_filter_active

# Calibrated on the 3162-row autosorter corpus (2026-08-16, reproducible via
# tests/manual/verify_similarity_corpus.py): 0.7 collapses 102 rows into 11
# families and splits the 115-row gate category into its ~10 genuinely distinct
# failure tails. The card's 0.95 was measured and rejected (77 rows, no
# grouping value); the change was notified per the letter-fix protocol.
DEFAULT_THRESHOLD = 0.7
# Trigram Jaccard is meaningless on short strings ("Bug 1" vs "Bug 2" scores
# ~0.8; two empty strings score 1.0 through the padding). Enforced in the
# SCORING layer so resolver, report and check share one policy.
MIN_TEXT_LEN = 40
MAX_ANNOTATIONS = 5
# File-time pool bound: an advisory needs recent history; completeness lives
# in the offline scrub. Measured: a 92-row category scan costs ~24 ms.
CANDIDATE_POOL_LIMIT = 500
_EXCERPT_LEN = 200
_MEMO_CAP = 2000

# ANSI color remnants survive in real descriptions both with the ESC byte and
# already stripped of it ("[0m[32m…" observed in the corpus).
_ANSI_REMNANT = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]{1,6}m")
_WS = re.compile(r"\s+")

# Annotation pool: live rows plus DECIDED rows — "resembles CB-N, already
# dismissed" is the most valuable annotation. `fixed` stays out: an exact
# match already reopens, and a near-match to a fixed card is ambiguous enough
# to belong in the offline scrub instead.
_ANNOTATE_STATUSES = LIVE_STATUSES + ("wont_fix", "not_a_bug")


def normalize_text(description: str, meta: dict[str, Any] | None = None) -> str:
    """Identity normalization plus similarity-only cleanup (ANSI remnants).

    The extra stripping lives HERE, not in the fingerprint normalization —
    that algorithm is versioned (auto:v1) and must not drift under an
    extension's needs.
    """
    text = _ANSI_REMNANT.sub(" ", normalized_identity_text(description, meta))
    return _WS.sub(" ", text).strip()


def trigram_set(text: str) -> frozenset[str]:
    padded = f" {text} "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _validate_score_params(threshold: float, limit: int | None = None) -> None:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    if limit is not None and limit < 0:
        # A negative limit would negative-slice and silently return the WORST
        # matches — refuse it.
        raise ValueError(f"limit must be >= 0, got {limit}")


def _parse_meta(meta_json: str | None) -> dict[str, Any]:
    """Tolerant parse over the accessor's raw meta_json — the ONE place.

    Invalid JSON and valid-but-non-dict JSON ("[1,2]", "3") both degrade to {}:
    legacy data must degrade the SCORE, never fail the caller.
    """
    try:
        meta = json.loads(meta_json) if meta_json else {}
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


_trigram_memo: dict[tuple[str, str], frozenset[str]] = {}


def _row_trigrams(row: dict[str, Any]) -> frozenset[str]:
    """Memoized per (id, created_at-as-version): a bumped row re-normalizes.

    Keyed on (id, updated-marker) with a FIFO cap so batch adds pay for one
    scan, not N (measured: 92-row category = 23.7 ms/scan; a 100-member batch
    without the memo spends ~2.4 s inside one write-locked transaction).
    """
    key = (row["id"], row["created_at"])
    hit = _trigram_memo.get(key)
    if hit is not None:
        return hit
    tri = trigram_set(normalize_text(row["description"], _parse_meta(row["meta_json"])))
    if len(_trigram_memo) >= _MEMO_CAP:
        _trigram_memo.pop(next(iter(_trigram_memo)))
    _trigram_memo[key] = tri
    return tri
```

NOTE for the implementer on the memo key: the accessor exposes `created_at`, not `updated_at`.
A bump changes `updated_at` but not the description, so `created_at` is the correct cheap
version key for TRIGRAMS (descriptions are immutable on findings — CB-21 records `description`
as not updatable). If CB-21 ever makes descriptions mutable, add `updated_at` to the accessor
and the key. State this in a comment.

`find_similar` and `group_report`:

```python
def find_similar(
    conn: sqlite3.Connection,
    *,
    description: str,
    category: str,
    meta: dict[str, Any] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = MAX_ANNOTATIONS,
) -> list[dict[str, Any]]:
    """Annotation-pool findings whose text scores >= threshold, best first.

    Applies EXACTLY the resolver's policy (pool, MIN_TEXT_LEN, threshold) —
    similarity_check exposes this, so a filer can preview what would be stamped.
    """
    _validate_score_params(threshold, limit)
    query_norm = normalize_text(description, meta)
    if len(query_norm) < MIN_TEXT_LEN:
        return []
    query_tri = trigram_set(query_norm)
    rows = similarity_candidates(
        conn, category=category, statuses=_ANNOTATE_STATUSES,
        limit=CANDIDATE_POOL_LIMIT, order="newest",
    )
    scored = []
    for row in rows:
        tri = _row_trigrams(row)
        norm_len_ok = len(normalize_text(row["description"], _parse_meta(row["meta_json"])))
        # NOTE: implement the length gate WITHOUT double normalization — compute
        # the normalized text once, gate on it, then trigram it (refactor
        # _row_trigrams to return (norm, tri) or gate inside it).
        if norm_len_ok < MIN_TEXT_LEN:
            continue
        score = jaccard(query_tri, tri)
        if score >= threshold:
            scored.append({"id": row["id"], "score": round(score, 3),
                           "status": row["status"]})
    scored.sort(key=lambda m: (-m["score"], m["id"]))
    return scored[:limit]
```

(The implementer resolves the double-normalization note by having the memo store
`(norm_text, trigrams)` and gating on `len(norm_text)` — one normalization per row, cached.)

```python
class _DSU:
    def __init__(self, ids: list[str]):
        self._parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def group_report(
    conn: sqlite3.Connection,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    category: str | None = None,
    status: str | None = None,
    vectors: dict[str, list[float]] | None = None,
    family_limit: int | None = None,
    member_limit: int | None = None,
) -> dict[str, Any]:
    """Offline scrub: similarity families as AUDITABLE evidence (CB-46's dry run).

    READ-ONLY; no merge performed or implied. Families are connected components
    — A~B and B~C do not imply A~C, so every family carries min_pair_score (its
    chain quality) and the full edge list; CB-46 sets its own diameter bar from
    the evidence instead of trusting membership. Default population is the LIVE
    set (grouping decided rows into a merge dry run would contradict
    decision-stays-decided); status= widens explicitly and the response names
    its populations. Short rows are excluded by the scoring-layer MIN_TEXT_LEN
    policy and counted. A pair scores by cosine when BOTH members have vectors,
    lexically otherwise.
    """
    _validate_score_params(threshold, family_limit)
    if member_limit is not None and member_limit < 0:
        raise ValueError(f"member_limit must be >= 0, got {member_limit}")
    if is_text_filter_active(category) and not category.strip():
        raise ValueError("category filter must not be blank")
    if status is not None and status != "":
        rows = similarity_candidates(conn, category=category, status=status)
        populations = [rows[0]["status"]] if rows else [status]
    else:
        rows = similarity_candidates(conn, category=category, statuses=LIVE_STATUSES)
        populations = list(LIVE_STATUSES)

    recs, skipped = [], 0
    for row in rows:
        meta = _parse_meta(row["meta_json"])
        norm = normalize_text(row["description"], meta)
        if len(norm) < MIN_TEXT_LEN:
            skipped += 1
            continue
        recs.append({"row": row, "tri": trigram_set(norm),
                     "vec": (vectors or {}).get(row["id"])})

    dsu = _DSU([r["row"]["id"] for r in recs])
    edges: list[dict[str, Any]] = []
    blocks: dict[str, list[dict]] = defaultdict(list)
    for rec in recs:
        blocks[rec["row"]["category"]].append(rec)
    for block in blocks.values():
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]
                if a["vec"] is not None and b["vec"] is not None:
                    score = cosine(a["vec"], b["vec"])
                else:
                    score = jaccard(a["tri"], b["tri"])
                if score >= threshold:
                    dsu.union(a["row"]["id"], b["row"]["id"])
                    edges.append({"a": a["row"]["id"], "b": b["row"]["id"],
                                  "score": round(score, 3)})

    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recs:
        members[dsu.find(rec["row"]["id"])].append(rec["row"])
    fam_ids = {root: {m["id"] for m in fam} for root, fam in members.items() if len(fam) > 1}
    families = []
    for root, fam in members.items():
        if len(fam) <= 1:
            continue
        fam_edges = [e for e in edges if e["a"] in fam_ids[root]]
        fam_sorted = sorted(fam, key=lambda m: (m["created_at"], m["id"]))
        families.append({
            "category": fam_sorted[0]["category"],
            "size": len(fam_sorted),
            "min_pair_score": min(e["score"] for e in fam_edges),
            "edge_count": len(fam_edges),
            "edges": fam_edges,
            "members": [
                {"id": m["id"], "status": m["status"], "severity": m["severity"],
                 "occurrence_count": m["occurrence_count"], "created_at": m["created_at"],
                 "file": m["file"],
                 "description_excerpt": m["description"][:_EXCERPT_LEN]}
                for m in fam_sorted
            ],
        })
    families.sort(key=lambda f: (-f["size"], f["members"][0]["id"]))
    families_total = len(families)
    members_total = sum(f["size"] for f in families)
    if family_limit is not None:
        families = families[:family_limit]
    if member_limit is not None:
        for f in families:
            f["members"] = f["members"][:member_limit]
    return {
        "threshold": threshold,
        "populations": populations,
        "rows_considered": len(recs) + skipped,
        "rows_skipped_short": skipped,
        "collapse_count": sum(f["size"] - 1 for f in families) if family_limit is None
        else members_total - families_total,
        "families": families,
        "families_total": families_total,
        "members_total": members_total,
    }
```

NOTE for the implementer: `collapse_count` must count over ALL families regardless of
`family_limit` (truncation changes the page, not the statistic) — compute it from the
pre-truncation list; the conditional above expresses that, simplify to one expression computed
before truncation. Status-widened populations: when `status=` is given, resolve it once via
the accessor's own resolution (populations = [resolved value]); do not re-implement resolution
in similarity.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest tests/test_similarity.py -v`
Expected: all PASS (TestAnnotateResolver comes in Task 4).

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/similarity.py tests/test_similarity.py
git commit -m "feat(similarity): lexical detector + auditable grouping report (CB-45)"
```

### Task 4: the file-time annotate resolver

**Files:**
- Modify: `src/codebugs/similarity.py` (resolver + registration at module level)
- Modify: `src/codebugs/db.py` (`_ensure_modules_loaded`: add `similarity`)
- Test: `tests/test_similarity.py` (extend)

**Interfaces:**
- Consumes: Task 1 seam, Task 3 `find_similar`.
- Produces: `meta.similar_to = [{"id", "score", "status"}]` on qualifying new findings.

- [ ] **Step 1: Write the failing tests (append)**

```python
class TestAnnotateResolver:
    def test_second_similar_add_gets_similar_to(self, conn):
        first = findings.add_finding(conn, severity="low", category="gate", file="f",
                                     description=LONG_A)
        second = findings.add_finding(conn, severity="low", category="gate", file="f",
                                      description=LONG_A2)
        assert second["was_new"] is True  # near-duplicate, NOT identical: no dedup
        [entry] = second["meta"]["similar_to"]
        assert entry["id"] == first["id"]
        assert entry["status"] == "open"
        assert entry["score"] == pytest.approx(0.96, abs=0.02)

    def test_first_add_has_no_annotation(self, conn):
        first = findings.add_finding(conn, severity="low", category="gate", file="f",
                                     description=LONG_A)
        assert "similar_to" not in first["meta"]

    def test_short_description_not_annotated(self, conn):
        findings.add_finding(conn, severity="low", category="gate", file="f",
                             description="Bug 1x")
        second = findings.add_finding(conn, severity="low", category="gate", file="f",
                                      description="Bug 2x")
        assert "similar_to" not in second["meta"]

    def test_dissimilar_not_annotated(self, conn):
        findings.add_finding(conn, severity="low", category="gate", file="f",
                             description=LONG_A)
        second = findings.add_finding(conn, severity="low", category="gate", file="f",
                                      description=LONG_B)
        assert "similar_to" not in second["meta"]

    def test_dismissed_candidate_annotated_with_status(self, conn):
        first = findings.add_finding(conn, severity="low", category="gate", file="f",
                                     description=LONG_A)
        findings.update_finding(conn, first["id"], status="not_a_bug")
        # a not_a_bug fingerprint hit files a NEW row (recurrence path) when the
        # descriptions are identical — use the near-duplicate so dedup stays out
        second = findings.add_finding(conn, severity="low", category="gate", file="f",
                                      description=LONG_A2)
        [entry] = second["meta"]["similar_to"]
        assert entry["id"] == first["id"] and entry["status"] == "not_a_bug"

    def test_caller_cannot_spoof_similar_to(self, conn):
        with pytest.raises(ValueError, match="similar_to"):
            findings.add_finding(conn, severity="low", category="gate", file="f",
                                 description="d", meta={"similar_to": []})

    def test_rescrub_can_rewrite_similar_to_via_update(self, conn):
        # Review SERIOUS-10: the reservation is add-side only — an unrepairable
        # annotation would be the CB-26 shape.
        first = findings.add_finding(conn, severity="low", category="gate", file="f",
                                     description=LONG_A)
        updated = findings.update_finding(conn, first["id"],
                                          meta_update={"similar_to": []})
        assert updated["meta"]["similar_to"] == []

    def test_suite_annotation_ratchet(self, conn):
        # Cheap ratchet (review CX-missing-9): the fixtures in THIS file are the
        # only long-similar no-id adds; if future fixtures start acquiring
        # annotations unintentionally, this count moves and the diff shows it.
        findings.add_finding(conn, severity="low", category="gate", file="f",
                             description=LONG_A)
        findings.add_finding(conn, severity="low", category="gate", file="f",
                             description=LONG_A2)
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM findings, json_each(findings.meta) "
            "WHERE json_each.key = 'similar_to'"
        ).fetchone()["n"]
        assert n == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_similarity.py::TestAnnotateResolver -v`
Expected: FAIL — no `similar_to` stamped, spoof not refused.

- [ ] **Step 3: Implement the resolver and load the module**

Append to similarity.py:

```python
def _annotate_resolver(
    conn: sqlite3.Connection, observation: dict[str, Any]
) -> dict[str, Any] | None:
    """Pre-add resolver: stamp advisory near-match candidates on a new finding.

    Fires only for genuine no-explicit-id inserts with annotate=True (the
    seam's firing rule, enforced in findings._add_one). Exists in addition to
    the similarity_check tool because the population that generated the
    115-row family is auto-filers that never call a preview tool — annotation
    must not depend on the filer opting in. Returns None rather than an empty
    list so unannotated rows stay clean.
    """
    matches = find_similar(
        conn,
        description=observation["description"],
        category=observation["category"],
        meta=observation["meta"],
    )
    if not matches:
        return None
    return {"similar_to": matches}


db.register_pre_add_resolver("similarity.annotate", _annotate_resolver,
                             meta_keys=("similar_to",))
```

In `db.py` `_ensure_modules_loaded`, add `similarity` to the imported module list (match the
existing style at db.py:794-804).

- [ ] **Step 4: Run the file, then the FULL suite**

Run: `uv run python -m pytest tests/test_similarity.py -v && uv run python -m pytest tests/ -q`
Expected: PASS. Blast-radius note (review-measured): only 2 of 217 existing test description
literals clear `MIN_TEXT_LEN`, so existing-suite disruption should be ~nil; any test that DOES
break gets its expectation fixed or an explicit id — record each in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/similarity.py src/codebugs/db.py tests/test_similarity.py
git commit -m "feat(similarity): file-time annotate resolver, meta.similar_to (CB-45)"
```

### Task 5: MCP tools, CLI (full bodies), mode registration, golden schema

**Files:**
- Modify: `src/codebugs/similarity.py` (register_tools / register_cli + provider registrations)
- Modify: `src/codebugs/server.py` (`SERVER_NAMES`), `src/codebugs/cli.py` (`--mode` allowlist)
- Test: `tests/test_similarity.py` (extend); regenerate `tests/golden/mcp_schema.json`

**Interfaces:**
- Produces: MCP tools `similarity_check`, `similarity_report`; CLI verbs `similarity-check`,
  `similarity-report`; mode slug `similarity`.

- [ ] **Step 1: Write the failing tests (append)**

```python
class TestSurfaces:
    def test_tool_provider_registered(self):
        assert any(p.name == "similarity" for p in db.get_tool_providers(mode="all"))

    def test_cli_provider_registered(self):
        assert any(p.name == "similarity" for p in db.get_cli_providers(mode="all"))

    def test_tools_forward_arguments(self, conn, tmp_path):
        # Review CX-missing-6 / CB-28's class: a declared argument must reach its
        # query — provider presence proves nothing; CALL the tools.
        captured = {}

        class FakeMCP:
            def tool(self):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

        import contextlib

        @contextlib.contextmanager
        def conn_factory():
            yield conn

        similarity.register_tools(FakeMCP(), conn_factory)
        findings.add_finding(conn, severity="low", category="gate", file="f",
                             description=LONG_A, finding_id="CB-1")
        out = captured["similarity_check"](description=LONG_A2, category="gate",
                                           threshold=0.9, limit=1)
        assert [m["id"] for m in out["matches"]] == ["CB-1"]
        strict = captured["similarity_check"](description=LONG_A2, category="gate",
                                              threshold=0.999)
        assert strict["matches"] == []  # threshold actually forwarded
        report = captured["similarity_report"](threshold=0.999)
        assert report["families"] == []  # forwarded here too
```

(Adapt the `conn_factory` shape to how `server._conn` actually behaves — read server.py; if
providers expect a plain callable returning a context manager, mirror it.)

- [ ] **Step 2: Implement the MCP tools**

```python
def register_tools(mcp, conn_factory) -> None:
    """Register similarity MCP tools."""

    @mcp.tool()
    def similarity_check(
        description: str,
        category: str,
        meta: dict[str, Any] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        limit: int = MAX_ANNOTATIONS,
    ) -> dict[str, Any]:
        """Preview what the file-time annotator would stamp for an observation.

        Applies EXACTLY the resolver's policy: same candidate pool (live +
        dismissed, same category, newest 500), same MIN_TEXT_LEN gate, same
        scoring. Advisory only — nothing is written.
        """
        with conn_factory() as conn:
            matches = find_similar(conn, description=description, category=category,
                                   meta=meta, threshold=threshold, limit=limit)
        return {"matches": matches, "threshold": threshold}

    @mcp.tool()
    def similarity_report(
        threshold: float = DEFAULT_THRESHOLD,
        category: str | None = None,
        status: str | None = None,
        family_limit: int | None = None,
        member_limit: int | None = None,
    ) -> dict[str, Any]:
        """Offline grouping scrub: similarity families as auditable evidence.

        Families are connected components with min_pair_score and edge lists —
        the dry run for any future backfill/merge (CB-46); no merge is
        performed or implied. Default population is LIVE rows; pass status=
        to widen. Wall-clock is quadratic per category block (~115k pair
        comparisons on a 3k-row tracker); prefer the CLI for very large
        trackers.
        """
        with conn_factory() as conn:
            return group_report(conn, threshold=threshold, category=category,
                                status=status, family_limit=family_limit,
                                member_limit=member_limit)


db.register_tool_provider("similarity", register_tools)
```

- [ ] **Step 3: Implement the CLI — FULL body (review CX-smell-10: the error-arm ordering is
  load-bearing and must be reviewable)**

```python
def register_cli(sub, commands) -> None:
    """Register similarity CLI commands."""
    import argparse  # match the module's existing import placement conventions

    p_check = sub.add_parser("similarity-check",
                             help="Preview what the file-time annotator would stamp")
    p_check.add_argument("--description", required=True)
    p_check.add_argument("--category", required=True)
    p_check.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_check.add_argument("--limit", type=int, default=MAX_ANNOTATIONS)
    p_check.add_argument("--json", action="store_true", dest="as_json")

    p_report = sub.add_parser("similarity-report",
                              help="Offline similarity grouping report (CB-46 dry run)")
    p_report.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_report.add_argument("--category", default=None)
    p_report.add_argument("--status", default=None)
    p_report.add_argument("--family-limit", type=int, default=None)
    p_report.add_argument("--member-limit", type=int, default=None)
    p_report.add_argument("--json", action="store_true", dest="as_json")

    def cmd_similarity_check(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            matches = find_similar(conn, description=args.description,
                                   category=args.category, threshold=args.threshold,
                                   limit=args.limit)
        except json.JSONDecodeError as e:
            # Ordering is load-bearing (the _cmd_update pattern): a stored-meta
            # parse failure after reads is NOT bad input; JSONDecodeError
            # subclasses ValueError, so this arm must come first.
            raise e
        except (KeyError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        if args.as_json:
            print(json.dumps({"matches": matches}, indent=2))
        elif not matches:
            print("No similar findings.")
        else:
            rows = [[m["id"], m["status"], f"{m['score']:.3f}"] for m in matches]
            print(fmt.table(["ID", "STATUS", "SCORE"], rows))

    def cmd_similarity_report(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            report = group_report(conn, threshold=args.threshold, category=args.category,
                                  status=args.status, family_limit=args.family_limit,
                                  member_limit=args.member_limit)
        except json.JSONDecodeError as e:
            raise e
        except (KeyError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        if args.as_json:
            print(json.dumps(report, indent=2))
            return
        print(f"threshold={report['threshold']} populations={report['populations']} "
              f"rows={report['rows_considered']} skipped_short={report['rows_skipped_short']} "
              f"collapse={report['collapse_count']}")
        if not report["families"]:
            print("No similarity families.")
            return
        for fam in report["families"]:
            print(f"\n[{fam['category']}] size={fam['size']} "
                  f"min_pair_score={fam['min_pair_score']:.3f} edges={fam['edge_count']}")
            rows = [[m["id"], m["status"], m["severity"], str(m["occurrence_count"]),
                     m["description_excerpt"][:60]] for m in fam["members"]]
            print(fmt.table(["ID", "STATUS", "SEV", "OCC", "DESCRIPTION"], rows))
        if report["families_total"] > len(report["families"]):
            print(f"\n({report['families_total'] - len(report['families'])} more families "
                  f"truncated; --family-limit)")

    commands["similarity-check"] = cmd_similarity_check
    commands["similarity-report"] = cmd_similarity_report


db.register_cli_provider("similarity", register_cli)
```

(Read `fmt.py` and one existing `register_cli` — bench.py — before writing; match the ACTUAL
table-helper name and the commands-dict wiring. `sys`, `fmt` imports at module top. The
`json.JSONDecodeError` arms re-raise deliberately: a parse failure of STORED data after reads is
not user input error — a traceback is honest there, exit-1-with-tidy-message is not.)

- [ ] **Step 4: Mode registration + golden schema**

Add `"similarity"` to `SERVER_NAMES` in server.py (value matching the naming of the other
entries) and to cli.py's `--mode` choices if it is a separate literal list (verify).

Run: `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`
(from the worktree per the CLAUDE.md warning), then the full suite + lint:
`uv run python -m pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/similarity.py src/codebugs/server.py src/codebugs/cli.py tests/golden/mcp_schema.json tests/test_similarity.py
git commit -m "feat(similarity): MCP tools, full CLI, mode registration (CB-45)"
```

### Task 6: Reproducible corpus verification (mandatory fix 14)

**Files:**
- Create: `tests/manual/verify_similarity_corpus.py` (committed — the calibration evidence must
  be reproducible; scratchpad-only measurement was a review finding)

- [ ] **Step 1: Write the committed script**

```python
"""Reproduce the CB-45 similarity calibration against a real tracker corpus.

Not collected by pytest (tests/manual/); run explicitly:
    uv run python tests/manual/verify_similarity_corpus.py [db_path]

Exact tolerance (review mandate): at threshold 0.7 on the 2026-08-16 autosorter
corpus, family count and collapse count must match EXACTLY (11 families, 102
rows collapse; gate category 111/115 grouped); only total row counts may drift
as the live corpus grows. A deviation in family/collapse counts means the
shipped normalization diverged from the calibrated one — investigate, never
hand-wave.
"""

import sqlite3
import sys

sys.path.insert(0, "src")
from codebugs import similarity  # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/faxik/w/autosorter/.codebugs/findings.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# The calibration measured ALL statuses (the corpus is mostly terminal rows);
# widen deliberately, per-status, to match.
report = None
for status in ("open", "in_progress", "stale", "fixed", "wont_fix", "not_a_bug"):
    part = similarity.group_report(conn, status=status)
    print(f"status={status}: rows={part['rows_considered']} "
          f"collapse={part['collapse_count']} families={len(part['families'])}")

full = similarity.group_report(conn, status=None)  # NOTE: see below
print(f"\nALL-STATUS totals: rows={full['rows_considered']} "
      f"collapse={full['collapse_count']} families={full['families_total']}")
for fam in full["families"][:12]:
    print(f"  n={fam['size']:3d} min_pair={fam['min_pair_score']:.3f} "
          f"cat={fam['category'][:32]} ids={[m['id'] for m in fam['members'][:3]]}")
```

NOTE for the implementer: `group_report(status=None)` defaults to LIVE-only after this plan —
the calibration corpus was measured across ALL statuses. Give `group_report` an explicit
`statuses=` passthrough (mirroring the accessor) or an `"all"` sentinel value, decided at
implementation and used here; do NOT silently compare live-only numbers against the all-status
calibration.

- [ ] **Step 2: Run and verify**

Run: `uv run python tests/manual/verify_similarity_corpus.py`
Expected: all-status totals reproduce the calibration (11 families / 102 collapse, modulo pure
row-count growth). Record the actual numbers verbatim — they go into the ledger and the CB-45
close note. Any family/collapse deviation: stop and investigate.

- [ ] **Step 3: Commit**

```bash
git add tests/manual/verify_similarity_corpus.py
git commit -m "test(similarity): committed corpus calibration verification (CB-45)"
```

### Task 7: Documentation, cards, hand-off

**Files:**
- Modify: `CLAUDE.md` — (a) replace the "Pre-add resolver seam: decided, not built (CB-44)"
  debt entry: the seam now EXISTS, built with its first consumer; annotate-only; redirect still
  deliberately inexpressible; never-commit ENFORCED (entry guard + post-resolver guard + guarded
  cleanup); reserved keys via `db.resolver_reserved_meta_keys()` (loads modules first);
  (b) a "Similarity extension" bullet: first self-registering non-domain module, zero SQL
  (findings.similarity_candidates is the sanctioned read), threshold 0.7 calibration + the
  rejected 0.95 with the notification record, MIN_TEXT_LEN in the scoring layer, `similar_to`
  reserved add-side only (update writable — CB-26's repair-path lesson), annotation pool includes
  dismissed rows with status stamped, firing rule `finding_id is None and annotate`, CSV import
  passes `annotate=False` and strips the dynamic reserved union;
  (c) the requirements-parity decision (D5) appended to the findings-identity bullet;
- Modify: `.claude/plans/BUGFIX-LOOP-LEDGER.md` — 2026-08-16 CB-45 section: honest numbers
  (102 vs 71; 0.95 rejected with the subfamily evidence; 43-family min_pair 0.393 and why it is
  still one defect), the review verdict (6.5/10, 14 mandatory fixes, cross-model scorecard).
- Modify: `.claude/plans/2026-08-16-identity-dedup-HANDOFF.md` — superseded pointer, or new
  handoff per session convention.
- Cards: close CB-45 (`fixed`, note with merge SHA + measured numbers + D5 decision + threshold
  rejection + notification record); annotate CB-46 (still blocked; the scrub report now exists
  with edges/min_pair_score — name the MCP tool; D6's proposed-not-ratified policy including
  blocker links in the dangling-reference list); release the CB-45 claim.
- Operational: reinstall the pipx `codebugs-mcp` from the repo after merge; note that a
  long-running server serves old code until restarted.

- [ ] **Step 1: Apply the documentation edits above**
- [ ] **Step 2: Full suite + lint one last time; commit docs**

```bash
git add CLAUDE.md .claude/plans/
git commit -m "docs: similarity extension + seam rules, CB-45 close prep"
```

---

## Process gates (from the user's standing rules)

1. ~~Before Task 1: adversarial-review-x2~~ — **DONE** (verdict 6.5/10; all 14 mandatory fixes
   encoded in this revision; appendix below).
2. Implementation in a worktree (`superpowers:using-git-worktrees`), branch
   `feat/cb-45-similarity-seam`.
3. **Before merge**: cross-model diff review (codex-code-review) — merge-gating change to core
   write-path files (db.py, findings.py).
4. Merge via the repo's own discipline; close cards; ledger; handoff; pipx reinstall.

## Self-Review (writing-plans checklist, re-run after encoding the fixes)

- Fix coverage: all 14 mandatory fixes have a home — 1→Task 2 (accessor), 2→Task 1 (contract +
  hostile tests), 3→Task 3 (report shape + scoring-layer guard), 4→D2+Task 2 (firing rule +
  annotate flag), 5→Task 1 tests, 6→Task 1 `_validate_resolver_outcome`, 7→Task 1
  `resolver_reserved_meta_keys`, 8→Task 2 CSV strip, 9→Task 2 `_validate_meta_keys(updating=)`,
  10→Task 3 memo + Task 2 batch test, 11→Task 5 full CLI, 12→D2/D4/D6 + Task 5 call-tests,
  13→D0, 14→Task 6. Recommended fixes: dismissed-rows pool (Task 3/4), read-side status
  (report carries member status; full read-side resolution deferred out loud — display surfaces
  are not in this plan's scope), cosine dim check (Task 3), vector narrowing (D4), cross-category
  honesty (D1), failure-tail probe (D1 notes it; automation deferred to CB-46's scrub usage,
  named in the card note).
- Known deliberate deferrals, stated out loud: read-side `similar_to` status resolution in
  display surfaces (no display surface exists in this plan); automated failure-tail probe
  (CB-46's sample-audit step consumes the edges the report now emits); unblocked cross-category
  measurement (category block restated as a cost bound in D1, not evidence).
- Type consistency pass: `similar_to` entries `{"id","score","status"}` in resolver, tests, D4;
  accessor keys match every consumer; `at` naming unified; firing predicate `finding_id is None
  and annotate` identical in D2, Task 2 code, Task 4 docstring. Memo key uses `created_at` with
  the CB-21 caveat stated in place.
- End-to-end read after encoding: done as one pass. Composition seams found and fixed in place:
  Task 6's population mismatch (calibration = all statuses vs report default = live) — resolved
  with the explicit `statuses=` passthrough note; `collapse_count` under truncation — resolved
  with the pre-truncation rule.

---

## Adversarial Review x2 Corrections (appendix)

Verdict: health 6.5/10 — "significant but well-scoped rework, then ship"; the ratified scope
survived every attack untouched; 14 mandatory fixes, all encoded above. Attackers: Opus adversary
(33 findings) + Codex GPT-5.6 Sol (confidence 0.98, reproduced the corpus numbers indepen-
dently); Opus defender with fresh measurements; Opus judge with direct spot-checks.

**Corroborated by both models (settled, high-confidence):** broken never-commits test; findings-
table reach-in (→ `similarity_candidates` accessor); unresolved `_LIVE_STATUSES` private import;
runner commits outside a transaction (SAVEPOINT/RELEASE = commit — both models verified
empirically); import-order-dependent reserved keys; non-serializable outcomes aborting the add;
missing MIN_TEXT_LEN in report/check; nondeterministic report output; union-find chaining
(43-family min pair 0.393) undermining the dry run; write-lock cost (batch ×N); permanently
unrepairable `similar_to`; test-registry order coupling.

**Codex-only catches (the cross-model value):** the CSV firing-rule justification was factually
FALSE (import passes no `finding_id` — Opus had it on its unfinished-checks list); a resolver can
destroy the caller's transaction through the raw connection (the review's sharpest finding);
annotated CSV round-trip refused by the new validation; valid non-dict JSON meta crashing
scoring; terminal-decision rows excluded from the pool losing the most valuable annotation
(best pure-design insight); circular category-blocking evidence; half-implemented vector
promise; the quantified 0.393 min-pair score.

**Opus-only catches:** report groups terminal rows (decision-stays-decided breach); cleanup
masking the real error / losing the finding; Task 6 unfalsifiable (different normalization +
scratchpad-only script); the embeddings-precedent overclaim; the ★-authority analysis (threshold
= permitted letter-fix missing its required notification — now D0); the prose firing rule being
actively wrong (`dedup_action=="created"` includes explicit-id inserts); hook-vs-provider
registry discipline archaeology.

**Defender measurements that defused attacks:** calibration reproduces identically through the
shipped normalization (11 families / 102 collapse, ANSI on or off); the 43-row family is ONE
defect (all 43 share the WAL-checkpoint failure tail — Codex's "different failing tests" did not
survive inspection); write-lock cost measured at 23.7 ms/add (tolerable) vs ~2.4 s/100-batch
(the real hazard); suite blast radius ~nil (2 of 217 test literals clear MIN_TEXT_LEN).

**Dismissed:** requirements-parity "reversal" (the card delegates the decision verbatim —
judge read the card); `dict | None` MCP convention complaint (findings' own `add` uses it);
cli.py line-length reformat obligation (E501 not enforced); Codex's false-merge accusation
against the 43-family (structure point stood, the specific accusation did not).
