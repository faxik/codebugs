# Rationale — architecture

Biography for the corresponding rules in `CLAUDE.md`: review rounds, reproduced incidents,
rejected forms and the measurements a decision was made on. **No rule lives here.** A line
in this file that reads as an instruction is a defect, and its place is the rules layer.

---

### Known architectural debt — CB-26, CB-31, CB-45 {#известный-архитектурный-долг}

**Justifies the rules** in `CLAUDE.md` → `## Architecture` → `### Known architectural debt`.

**Two entries that outlived the code they described.** The staleness/provenance entry claimed the
logic still sat in `db.py` long after it had moved (CB-4), and the `blockers.py` entry described a
private `_row_to_dict` reach that no longer existed anywhere in the package (CB-5).

**Where the resolver seam's three enforcement clauses came from.** The refusal to run outside an open
transaction, the detection of a resolver that closed the caller's transaction, and the guarded
`ROLLBACK TO` cleanup were all three cross-model review findings. The per-call NONCE in the savepoint
name came from a Codex diff review, which showed that index-only naming let a resolver commit and
recreate the runner's savepoint by its predictable name.

**The CB-26 measurement.** 19 of 23 open `stream/triage` rows pointed at terminal findings, so
`triage_inbox` was about 83% stale.

**What CB-31's first implementation broke.** It built the subquery inside `milestones/`, which
reached into another module's tables, bypassed the `readable_cols` allowlist, and carried a
`# noqa: S608` justified by validation in a file it did not own — three of this document's own rules,
broken by one function.

**Why the differential test's two odd rows are the whole of it.** Measured: the realistic NULL-unsafe
mutant is caught by the `external`-pointing-at-a-terminal-finding row and the `bug`-with-a-missing-
source row, and by nothing else — so an "externals are covered" fixture whose external points at a
*live* finding proves nothing.

**Cost of the per-row predicate, for the record.** `source_is_terminal` ran a `sqlite_master` probe
plus a status `SELECT` for every candidate row, and per-bucket construction of the replacement would
have added eight `sqlite_master` reads inside the exclusive-lock hold.
