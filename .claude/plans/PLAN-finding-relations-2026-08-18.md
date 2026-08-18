# PLAN — `finding_relations`: the relations table and its operations

**Scope: the CORE ONLY** — the table, three operations, registration, tests. It mutates no existing
data and adds no migration. Approved by the owner 2026-08-18.

**Split note.** Revisions 1 and 2 of this document also carried the legacy-meta migration. Across
two cross-model adversarial rounds, **every FATAL finding landed in the migration and none in the
core** — the core passed both. Holding a working ledger hostage to the migration's governance bought
no safety, so the migration moved to
[`PLAN-relations-migration-2026-08-18.md`](PLAN-relations-migration-2026-08-18.md), which carries its
twelve open defects and is explicitly not implementable yet.

---

## 1. Why

The tracker has no way to record that two cards are related. People have been writing it into ad-hoc
JSON `meta` keys for months — **164 distinct key names for roughly five concepts** — which cannot be
queried, and cannot be retracted (`findings.py:923-924` is `dict.update`, merge-only, so a `meta` key
can be overwritten but never removed). A relation that must be auditable and retractable needs a
table.

This surfaced from duplicate-merge work: closing a duplicate leaves no queryable record of *why*.

## 2. Schema

Module `src/codebugs/relations.py`, following the `blockers.py` idiom: schema constant at
`blockers.py:14`, indexes `:32-34`, `ensure_schema` `:40`, `register_schema` `:568-570`, tool
provider `:646`, CLI provider `:722`.

```sql
CREATE TABLE IF NOT EXISTS finding_relations (
  id INTEGER PRIMARY KEY,
  src_id TEXT NOT NULL,
  rel TEXT NOT NULL CHECK (rel IN
    ('duplicate_of','split_from','follow_up_of','found_during','distinct_from','related_to')),
  dst_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  note TEXT,
  retracted_at TEXT, retracted_by TEXT, retracted_reason TEXT,
  CHECK (src_id != dst_id),
  CHECK ((retracted_at IS NULL) = (retracted_by IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_live
  ON finding_relations(src_id, rel, dst_id) WHERE retracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_relations_src ON finding_relations(src_id);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON finding_relations(dst_id);
```

`CHECK(rel IN …)` matches the house idiom (`blockers.py:17-22` uses DB-level enums); leaving the
vocabulary in application code only was a review finding. The paired-nullability CHECK exists because
all three retraction columns are independently nullable otherwise, which lets a tombstone exist with
no actor — not an audited tombstone.

## 3. Vocabulary

`duplicate_of`, `split_from`, `follow_up_of`, `found_during`, `distinct_from`, `related_to`.

The first five are the RFC's ratified set (`RFC-identity-graph-2026-08-17.md` §S1a). `related_to` is
added because measurement showed ~700 of the corpus's edges are generic `related*` / `sibling*` with
no target term; the RFC's exclusions were about **ownership conflicts**, not minimalism, so a generic
symmetric term collides with nothing.

**Excluded, and enforced in `relate()`:**

- `recurrence_of` — core-owned. `_RESERVED_META_KEYS` (`findings.py:219`) is an exact frozenset of
  four names guarding the spoofing attack its comment describes.
- `blocked_by` — finding→finding blocking already ships in the blockers module with trigger and
  lifecycle semantics; `relate()` errors pointing at `blockers-add`.
- `similar_to` — annotator-owned (`similarity.py`), an advisory snapshot, never a fact.

**Symmetry is per-relation and is not cosmetic:**

- **Symmetric — `distinct_from`, `related_to`.** Stored in canonical orientation (lexicographic min
  as `src`), so the unique index enforces one edge per pair and no reader searches both directions.
- **Directed — `duplicate_of`, `split_from`, `follow_up_of`, `found_during`.**
  **`duplicate_of` is loser → survivor.** Duplicate-*ness* is symmetric; `duplicate_of` is not — it
  names which card dies. Canonicalising it inverts the survivor in **3 of 3 real cases**
  (`CB-878→CB-877`, `CB-2946→CB-2935`, `CB-2251→CB-2227`). The RFC grouped it with `distinct_from`
  in error; that was the highest-severity finding of review round 1.

## 4. Invariants

- **Endpoint EXISTENCE is validated in `relate()`** at the application layer via `EntityRef.exists()`
  (`entities.py:162`). A declared FK would be unreliable: `db._open` (`db.py:1054-1105`) sets only
  WAL and `busy_timeout`, while `findings.py:105`/`:139` runs `PRAGMA foreign_keys=OFF/ON` during the
  legacy status migration — so enforcement is per-connection nondeterministic. Existence, **not**
  liveness: a live card citing a closed one is a first-class case, and 62.8% of the corpus's edges
  point at closed cards.
- **Retraction is a tombstone, never a DELETE.** `unrelate()` sets `retracted_at/by/reason`; readers
  filter `retracted_at IS NULL`. The most dangerous write here is a wrong `distinct_from`, which
  suppresses a pair from every future discovery path, so it must stay auditable.
- **Contradiction guard:** reject a live `duplicate_of` where a live `distinct_from` exists on the
  same unordered pair, and vice versa. Also reject a reciprocal `duplicate_of` — both cards cannot be
  the loser.
- **`relate()` runs under `with db.txn(conn)`** — the reentrant abstraction at `db.py:484-515`.
  **Never a raw `BEGIN IMMEDIATE`**: `db.py:486` says so outright, and `merge.py:257` /
  `capacity.py:214` carry explicit "no raw BEGIN IMMEDIATE here now (CB-40)" notes, because assigning
  `isolation_level` commits the caller's open transaction.
- Re-relating a live edge is an idempotent no-op returning the existing row; the original
  `source`/`note` win. A second opinion is a note append, not an overwrite.
- **`distinct_from` is inert until a consumer reads it.** `similarity.py` groups via DSU over
  category blocks and reads no relations table, so nothing is suppressed today. Stated rather than
  implied — an earlier revision claimed suppression that does not exist.

## 5. Registration — three hardcoded lists, not one

There is no discovery. Omit any one and the feature is silently absent, with `CREATE TABLE IF NOT
EXISTS` raising no error to catch:

- `db.py:1029-1051` `_ensure_modules_loaded` — the domain import list;
- `server.py:201` `SERVER_NAMES` — MCP server naming;
- `cli.py:104` `--mode` choices.

`register_schema("relations", ensure_schema, depends_on=("findings",))` — signature confirmed at
`db.py:52-67`.

## 6. Surface

| tool | purpose |
|---|---|
| `relations_relate(src, rel, dst, source, note?)` | assert; validates, canonicalizes, guards |
| `relations_unrelate(src, rel, dst, retracted_by, reason)` | tombstone it |
| `relations_query(id?, rel?, include_retracted?)` | edges touching a card, both directions |

`source` and `retracted_by` are **caller-supplied**, never hardcoded — an audited ledger whose actor
is a placeholder is not audited. `relations_query` also answers `active_suppressions` (live
`distinct_from` edges) via `rel=`.

## 7. Tests (TDD — red first)

1. `CHECK(src_id != dst_id)` rejects a self-edge.
2. `CHECK(rel IN …)` rejects an unknown relation.
3. Symmetric canonicalization: `relate(B, related_to, A)` then `relate(A, related_to, B)` → one row.
4. **`duplicate_of` is NOT canonicalized**: `(CB-a, duplicate_of, CB-b)` and the reverse are distinct
   index keys — assert the stored orientation is exactly as passed, loser→survivor.
5. Reciprocal `duplicate_of` is rejected by the contradiction guard.
6. Endpoint validation rejects an absent `dst_id`; a **closed** `dst_id` is accepted.
7. `unrelate` tombstones; the unique index then permits re-relating the same pair.
8. Retraction columns are all-null or all-non-null (the paired CHECK).
9. Contradiction guard: `duplicate_of` refused where a live `distinct_from` exists, and vice versa.
10. `recurrence_of` and `blocked_by` are refused, with the pointer error text.
11. `relate()` under an ambient transaction does not commit it (CB-40 regression).
12. Idempotent re-relate returns the existing row and does not overwrite `source`/`note`.
13. Registry: `relations` is discoverable through `db.py`, `server.py` and `cli.py`.

The MCP wire gate is `tests/test_boundary.py::TestMcpWireSchema` (regen instructions at `:162`) plus
`tests/test_server.py:162` (every registered tool rejects an unknown argument). The golden-schema
regen is **one** reviewed commit.

## 8. Seeding

Once the table exists, **50 human-labelled edges** already exist in
`~/.cache/codebugs-identity/goldset-2026-08-17.json` and can be inserted by hand through
`relations_relate` with `source='goldset-2026-08-17'`. That is real value with no migration
machinery, and it exercises the surface against real data.

## 9. Out of scope

- **All legacy-meta migration** → `PLAN-relations-migration-2026-08-18.md`.
- **`relation_proposals`** — no producer exists; deferred with the migration.
- **`backlinks()` / `group_by="tag"` / CSV round-trip** (RFC §S1b) — separate stage. **Known and
  stated:** `finding_relations` has no CSV representation, so a tracker restored from its own export
  loses the ledger. Acceptable while the ledger is small and re-seedable; it is a blocker for the
  migration, not for the core.
- **Acting on any edge.** `duplicate_of` asserts; it merges nothing. Merge policy is CB-46, by hand.
- **Read-time suppression from `distinct_from`** — requires changing `similarity.py`; see §4.

---

## Appendix — review provenance

Two adversarial rounds, Opus adversary + Codex/GPT-5.6-Sol in parallel, Opus defender/judge.
Everything below was verified by direct Read before being applied.

**Core findings that shaped this document:**

- `duplicate_of` canonicalization inverts the survivor (Opus, round 1) — highest severity found.
- `parent` must map to `split_from`, not a new `part_of`: `grouping.py:85` already declares
  `LINEAGE_PARENT_KEYS = ("split_from", "parent")`, pinned by `tests/test_grouping.py:306` (Codex,
  round 1). Killed a vocabulary term and the acyclicity question with it. *(Applies to the migration;
  recorded here because it removed `part_of` from the vocabulary.)*
- Raw `BEGIN IMMEDIATE` reopens CB-40 (both, round 1) — inherited verbatim from the RFC.
- Registration is three hardcoded lists (Opus found `db.py`, Codex added `server.py` and `cli.py`).
- `rel TEXT` diverges from the house `CHECK` idiom; no actor contract; retraction columns
  independently nullable (Codex, rounds 1–2).
- `distinct_from` is inert — `similarity.py` reads no relations table (Codex, round 1).
- `_RESERVED_META_KEYS` is an exact set of four names, not a `recurrence_*` family (Opus, round 1) —
  an RFC claim that had gone unchecked.
- `PRAGMA foreign_keys=ON` is left set by the legacy migration, so "never enables" was wrong while
  the conclusion (validate in the application layer) was right (Codex, round 1).

**Process note, twice earned.** The raw `BEGIN IMMEDIATE` and the `duplicate_of` symmetry both came
verbatim from the RFC, **which had itself passed an adversarial review x2**. A review that passes an
artifact does not immunize the claims that artifact carries into its successors. Separately, two
defects in revision 2 (`anchor` left classified as `duplicate_of` in one section after being
corrected in another; a fixture pinning a retracted edge as live) came from applying fixes
**section-locally without an end-to-end read** — the documented failure mode, reproduced.
