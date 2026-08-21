"""CB-21 parity gate: every column of an entity must be DECLARED, and every
declared write parameter must reach the MCP and CLI surfaces or declare its hole.

CB-21's defining property is invisibility from inside one file. `severity` was
write-once on findings while its sibling `priority` was already mutable on
requirements (CB-17); `description`/`category`/`file` are still immutable on
findings while `update_requirement` rewrites `description`; `source` is
INSERT-settable on both entities and named in neither update contract. Three
independent passes over the same function each found a DIFFERENT missing cell,
because prose is the wrong enforcement layer for a rule whose failure mode is a
gap nobody is looking at. This file is the enforcement layer.

It reads four sources per entity and refuses any column or parameter that is not
declared somewhere below:

1. ``PRAGMA table_info`` over a fresh in-memory schema (the columns that exist);
2. the keyword-only parameters of ``update_finding`` / ``update_requirement``
   (the writers the domain layer offers);
3. the MCP wrapper's signature, captured with a fake ``mcp`` (what a tool client
   can reach);
4. the CLI parser's argparse dests, captured with a fake ``sub`` (what an
   operator can type).

The shape is the ratchet-allowlist of
``tests/test_claims.py::test_24_no_plain_begin_ratchet``: the declarations are
module constants, and the gate's honest scope is stated rather than implied.

**What this gate does NOT do — stated because a gate described better than it
behaves is worse than no gate:**

- It reads DECLARED SIGNATURES, never function bodies. A parameter declared,
  surfaced, and then dropped on the floor inside the handler passes here; that
  is CB-28's axis ("a declared argument must reach its query"), and no signature
  reader can see it.
- It therefore does NOT prove that ``MUTABLE[column]``'s parameters actually
  write THAT column. The mapping is an assertion by the person who edited this
  file; what the gate enforces is that the assertion EXISTS and is total.
- The CLI dests are read from argparse's PRIVATE ``parser._actions``. There is
  no public accessor; if a future argparse removes it, repair this reader.
- It sees the parsers ``register_cli`` builds, not the parse-time behaviour: a
  ``dest=`` override, a mutually-exclusive group, or a subparser nested inside
  the verb's own parser would be read as plain dests.
- Only two entities are covered — the two with a public ``update_*`` function.
  A third entity gaining one acquires no coverage until it is added here.
- It says nothing about DEFAULTS, types, or vocabulary resolution on either
  side. Those are CB-19's and CB-25's axes.
"""

from __future__ import annotations

import argparse
import inspect
import sqlite3

import pytest

from codebugs import findings, reqs

# --------------------------------------------------------------------------
# Declarations. Adding a column to a schema without adding it here is a test
# failure by construction (the totality check), and that is the whole point.
# --------------------------------------------------------------------------

# column -> the update_* parameters that write it. `meta` is written by three
# arguments on findings and two on requirements; they compose over ONE dict and
# emit exactly one `meta = ?` (CB-16), which is why one column maps to many
# parameters rather than the reverse.
FINDINGS_MUTABLE: dict[str, tuple[str, ...]] = {
    "status": ("status",),
    "severity": ("severity",),
    "tags": ("tags",),
    "meta": ("notes", "append_note", "meta_update"),
    "reported_at_ref": ("reported_at_ref",),
}

REQS_MUTABLE: dict[str, tuple[str, ...]] = {
    "section": ("section",),
    "description": ("description",),
    "priority": ("priority",),
    "status": ("status",),
    "test_coverage": ("test_coverage",),
    "tags": ("tags",),
    "meta": ("notes", "meta_update"),
}

# column -> WHY it is not reachable from update_*.
#
# **Declaring a column immutable pins the CURRENT contract and its reason. It is
# not a verdict that the column may never become mutable.** The question CB-21
# actually raises — should `description` / `category` / `file` become mutable on
# findings, closing the CB-17 asymmetry with `update_requirement`? — stays OPEN.
# This gate does not answer it. It only forces the answer to be said out loud in
# one place, instead of being rediscovered by a fourth independent inspection of
# the same function.
FINDINGS_IMMUTABLE: dict[str, str] = {
    "id": (
        "The primary key. An id IS the entity; rewriting one is a different "
        "operation (re-file, or import's fresh-local-id path, CB-51), never an "
        "update argument."
    ),
    "description": (
        "An INPUT to the derived auto:v1 fingerprint (CB-43 item 4 hashes "
        "category, file and the normalized description). Making it mutable "
        "makes update_finding a RE-KEY of identity, and re-keying is a separate "
        "negotiated contract — CB-43 item 6 says so, and CB-61 negotiated "
        "exactly one such operation (findings.normalize_categories), which "
        "issues its own UPDATE precisely so that no caller acquires a re-key by "
        "argument. Whether to open a second one for description is CB-21's open "
        "question, not a settled no."
    ),
    "category": (
        "The same fingerprint input as description, and since CB-60 also a "
        "NORMALIZED and MINT-GATED input: a mutable category would have to "
        "re-run normalize_category, the minting gate and the auto:v1 "
        "derivation, i.e. be a re-key. CB-61's normalize_categories is the one "
        "sanctioned re-key of this column and it is deliberately not routed "
        "through this function."
    ),
    "file": (
        "The third fingerprint input (CB-43 item 4). Same re-key argument as "
        "description and category; a moved file is today re-observed, not "
        "edited in place."
    ),
    "source": (
        "First-reporter provenance, frozen by design: later observations' "
        "sources live only in the occurrence ring, and an imported "
        "observation's ring source can be a peer tracker's. Declared in words "
        "by BT-4 and RATIFIED BY THE OWNER 2026-08-20 (unit T-11, merge "
        "0466d44). This is CB-21's `source` cell, and it is CLOSED — do not "
        "reopen it as an oversight."
    ),
    "reported_at_commit": (
        "Immutable after insert, and update_finding's docstring has said so all "
        "along. Its mutable twin is reported_at_ref (a release is tagged after "
        "filing); do not confuse the pair."
    ),
    "created_at": "Stamped by the writer at insert, never supplied by a caller.",
    "updated_at": (
        "Stamped by the writer on every write, never supplied by a caller — an "
        "argument for it would let a caller lie about when the row moved."
    ),
    "fingerprint": (
        "The identity key itself (CB-43 item 6: INSERT-settable, documented "
        "immutable at update). Rewriting it must renegotiate "
        "ux_findings_fingerprint_live, which is what CB-61's "
        "normalize_categories does under its own boundaries."
    ),
    "occurrence_count": (
        "Observation ring state. Moved only by _bump_row on a dedup hit; a "
        "manual write would falsify the count of times the defect was seen."
    ),
    "last_seen_at": (
        "Observation ring state, same reason as occurrence_count — it records "
        "when the defect was last OBSERVED, not when the row was last edited "
        "(that is updated_at)."
    ),
}

REQS_IMMUTABLE: dict[str, str] = {
    "id": (
        "The primary key, and requirements are authored artifacts with "
        "caller-assigned ids on every write path (CB-45). Re-id is a re-author, "
        "not an update."
    ),
    "source": (
        "First-reporter provenance, the same freeze and the same 2026-08-20 "
        "owner ratification as its findings twin. CB-21 named `source` on BOTH "
        "entities; both cells are closed by that decision."
    ),
    "embedding": (
        "Not authored content: a packed vector BLOB owned by embeddings.py and "
        "written only by store_embedding / batch_store_embeddings (MCP "
        "reqs_embed / reqs_batch_embed). It is not INSERT-settable either — "
        "add_requirement has no such parameter — so the "
        "settable-at-INSERT/settable-at-UPDATE parity rule does not reach it. "
        "The module split is ARCH-005; a text argument here would put a second "
        "writer on another module's derived column."
    ),
    "created_at": "Stamped by the writer at insert, never supplied by a caller.",
    "updated_at": (
        "Stamped by the writer on every write, never supplied by a caller. Note "
        "embeddings.py also stamps it, which is exactly why it is not an "
        "argument anywhere."
    ),
}

# (parameter, layer) -> why the parameter is not reachable on that surface.
# Layer is "mcp" or "cli". A hole declared here must be REAL: the gate refuses a
# stale entry whose parameter is in fact present, so this list cannot rot into
# permission to skip a surface.
#
# Every entry below is the CB-6 shape — the CLI is systematically a subset of
# MCP, because these verbs' primary caller is an agent over MCP and the CLI verb
# grew only the flags an operator was measured to type. There are no MCP holes
# on either entity today, and none is declared: an undeclared MCP hole is a test
# failure, which is the half that matters (CB-18 — a domain parameter is not
# reachable until the wrapper declares it).
FINDINGS_SURFACE_GAPS: dict[tuple[str, str], str] = {
    ("tags", "cli"): (
        "CB-6: the CLI update verb carries no --tags. A full-replace list "
        "argument needs a splitting convention (the add verb's comma split) and "
        "no operator demand has been measured; MCP callers have it."
    ),
    ("meta_update", "cli"): (
        "CB-6: no --meta-update on the CLI verb. It would need a JSON literal "
        "argument, and the CLI exposes the two meta arguments an operator "
        "actually types (--notes, --append-note) instead."
    ),
    ("reported_at_ref", "cli"): (
        "CB-6: no --reported-at-ref on the CLI verb. The sanctioned manual "
        "mutation (BT-4) is reachable over MCP, which is where release-tagging "
        "callers live."
    ),
}

REQS_SURFACE_GAPS: dict[tuple[str, str], str] = {
    ("section", "cli"): (
        "CB-6: reqs-update carries no --section, while reqs-add does. This one "
        "is a plain string with no encoding question, so it is the weakest of "
        "the three holes and the cheapest to close when someone wants it."
    ),
    ("tags", "cli"): "CB-6: no --tags on reqs-update, same reason as its findings twin.",
    ("meta_update", "cli"): (
        "CB-6: no --meta-update on reqs-update; --notes is the meta argument an "
        "operator types."
    ),
}

# (name, layer) -> why a surface argument that is NOT a column writer is there.
# Without this the reverse check (check 6) would refuse the entity-id argument
# every surface must take. Each entry is verified to be really present, so the
# list cannot be padded with names that do not exist.
FINDINGS_SURFACE_EXTRAS: dict[tuple[str, str], str] = {
    ("finding_id", "mcp"): "The entity id — which row to update, not a column write.",
    ("id", "cli"): "The entity id positional — which row to update, not a column write.",
}

REQS_SURFACE_EXTRAS: dict[tuple[str, str], str] = {
    ("req_id", "mcp"): "The entity id — which row to update, not a column write.",
    ("id", "cli"): "The entity id positional — which row to update, not a column write.",
}

ENTITIES = {
    "findings": {
        "module": findings,
        "table": "findings",
        "updater": findings.update_finding,
        "mcp_tool": "update",
        "cli_verb": "update",
        "mutable": FINDINGS_MUTABLE,
        "immutable": FINDINGS_IMMUTABLE,
        "gaps": FINDINGS_SURFACE_GAPS,
        "extras": FINDINGS_SURFACE_EXTRAS,
    },
    "requirements": {
        "module": reqs,
        "table": "requirements",
        "updater": reqs.update_requirement,
        "mcp_tool": "reqs_update",
        "cli_verb": "reqs-update",
        "mutable": REQS_MUTABLE,
        "immutable": REQS_IMMUTABLE,
        "gaps": REQS_SURFACE_GAPS,
        "extras": REQS_SURFACE_EXTRAS,
    },
}

MIN_REASON_LEN = 20


# --------------------------------------------------------------------------
# The four readers.
# --------------------------------------------------------------------------


def _schema_columns(module, table: str) -> set[str]:
    """Source 1: the columns a FRESH database actually has.

    Built by running the module's own ``ensure_schema`` — migrations included —
    rather than by parsing the SCHEMA string, so a column added by a migration
    is covered exactly like one added to the CREATE TABLE.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        module.ensure_schema(conn)
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
        cols = {row["name"] for row in rows}
    finally:
        conn.close()
    assert cols, f"{table}: PRAGMA table_info returned nothing — the fixture built no table"
    return cols


def _updater_params(fn) -> set[str]:
    """Source 2: the KEYWORD-ONLY parameters of the update function.

    Keyword-only by design: this package's convention is that everything after
    ``conn`` is keyword-only, so the positional tail is ``conn`` and the entity
    id — neither of which is a column write.
    """
    return {
        name
        for name, p in inspect.signature(fn).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


def _mcp_params(module, tool_name: str) -> set[str]:
    """Source 3: the MCP wrapper's declared parameters.

    Captured with a fake ``mcp`` that keeps the FUNCTION, not just its name —
    the name alone would prove the tool exists and nothing about what it can be
    passed, which is exactly CB-18's failure (a domain parameter sitting
    unreachable behind a wrapper that never declared it).
    """
    captured: dict[str, object] = {}

    class _Mcp:
        def tool(self, *a, **kw):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn

            return deco

    module.register_tools(_Mcp(), lambda: None)
    assert tool_name in captured, f"MCP tool {tool_name!r} is not registered by {module.__name__}"
    return set(inspect.signature(captured[tool_name]).parameters)


def _cli_dests(module, verb: str) -> set[str]:
    """Source 4: the argparse dests the CLI verb's parser declares.

    Reads the PRIVATE ``parser._actions`` — argparse offers no public accessor,
    and that limit is in this module's docstring rather than left to be
    discovered. The fake ``sub`` hands out real parsers so that dest derivation
    (``--append-note`` -> ``append_note``) is argparse's, not a re-implementation
    of it here.
    """
    parsers: dict[str, argparse.ArgumentParser] = {}

    class _Sub:
        def add_parser(self, name, **kw):
            p = argparse.ArgumentParser(add_help=False)
            parsers[name] = p
            return p

    module.register_cli(_Sub(), {})
    assert verb in parsers, f"CLI verb {verb!r} is not registered by {module.__name__}"
    return {a.dest for a in parsers[verb]._actions if a.dest != "help"}


def _declared_params(mutable: dict[str, tuple[str, ...]]) -> set[str]:
    return {p for params in mutable.values() for p in params}


ENTITY_IDS = sorted(ENTITIES)


@pytest.mark.parametrize("entity", ENTITY_IDS)
class TestColumnAxis:
    """Every column is declared exactly once, mutable or immutable-with-a-reason."""

    def test_declarations_are_total_over_the_schema(self, entity):
        """A new column in the schema fails this test until someone declares it.

        This is the check CB-21 exists for: the three passes that each missed a
        different cell were each reading one function, and no reading of one
        function can enumerate a table.
        """
        spec = ENTITIES[entity]
        columns = _schema_columns(spec["module"], spec["table"])
        mutable, immutable = set(spec["mutable"]), set(spec["immutable"])

        both = sorted(mutable & immutable)
        assert not both, f"{entity}: declared BOTH mutable and immutable: {both}"

        undeclared = sorted(columns - (mutable | immutable))
        assert not undeclared, (
            f"{entity}: schema columns declared nowhere: {undeclared}. Add each to "
            f"MUTABLE (naming the update_* parameters that write it) or to IMMUTABLE "
            f"(with the reason it is not reachable)."
        )

        phantom = sorted((mutable | immutable) - columns)
        assert not phantom, (
            f"{entity}: declared columns that the schema does not have: {phantom}"
        )

    def test_every_immutable_column_carries_a_real_reason(self, entity):
        """A blank or one-word reason is a declaration in form only."""
        for column, reason in ENTITIES[entity]["immutable"].items():
            assert isinstance(reason, str), f"{entity}.{column}: reason is not a string"
            assert len(reason.strip()) >= MIN_REASON_LEN, (
                f"{entity}.{column}: immutability reason is too short to be a reason: "
                f"{reason!r}"
            )


@pytest.mark.parametrize("entity", ENTITY_IDS)
class TestUpdaterAxis:
    """The declared parameters and the update function's signature agree, both ways."""

    def test_every_declared_parameter_exists_on_the_updater(self, entity):
        spec = ENTITIES[entity]
        actual = _updater_params(spec["updater"])
        for column, params in spec["mutable"].items():
            for param in params:
                assert param in actual, (
                    f"{entity}.{column} declares parameter {param!r}, but "
                    f"{spec['updater'].__name__} has no such keyword-only parameter."
                )

    def test_every_updater_parameter_is_declared_by_some_column(self, entity):
        """A new writing parameter with no column declaration fails here.

        The reverse of the check above, and the half that catches the real
        drift: someone adds ``file=`` to ``update_finding`` and the IMMUTABLE
        reason for ``file`` silently becomes a lie.
        """
        spec = ENTITIES[entity]
        declared = _declared_params(spec["mutable"])
        undeclared = sorted(_updater_params(spec["updater"]) - declared)
        assert not undeclared, (
            f"{entity}: {spec['updater'].__name__} takes parameters no column claims: "
            f"{undeclared}. Add each to the MUTABLE entry of the column it writes "
            f"(and move that column out of IMMUTABLE if it is there)."
        )


@pytest.mark.parametrize("entity", ENTITY_IDS)
@pytest.mark.parametrize("layer", ["mcp", "cli"])
class TestSurfaceAxis:
    """Declared writers reach MCP and the CLI, or the hole is declared with a reason."""

    @staticmethod
    def _surface(spec, layer) -> set[str]:
        if layer == "mcp":
            return _mcp_params(spec["module"], spec["mcp_tool"])
        return _cli_dests(spec["module"], spec["cli_verb"])

    def test_every_declared_parameter_is_present_or_declared_missing(self, entity, layer):
        spec = ENTITIES[entity]
        surface = self._surface(spec, layer)
        for param in sorted(_declared_params(spec["mutable"])):
            if param in surface:
                continue
            reason = spec["gaps"].get((param, layer))
            assert reason is not None, (
                f"{entity}: parameter {param!r} is missing from the {layer} surface and "
                f"the hole is not declared. Either expose it there (CB-18) or add "
                f"({param!r}, {layer!r}) to SURFACE_GAPS with the reason."
            )
            assert len(reason.strip()) >= MIN_REASON_LEN, (
                f"{entity}: the {layer} hole for {param!r} has no real reason: {reason!r}"
            )

    def test_no_declared_hole_is_stale(self, entity, layer):
        """A gap declared for a parameter that IS on the surface is a lie.

        Without this, closing a hole and forgetting to delete its entry would
        leave a permanent licence to remove the argument again silently.
        """
        spec = ENTITIES[entity]
        surface = self._surface(spec, layer)
        stale = sorted(p for (p, ell) in spec["gaps"] if ell == layer and p in surface)
        assert not stale, (
            f"{entity}: SURFACE_GAPS still declares {stale} missing from {layer}, but "
            f"they are present. Delete the stale entries."
        )

    def test_every_surface_argument_is_declared(self, entity, layer):
        """The reverse direction: an argument on the surface that no column claims.

        This is what refuses a surface argument added without a matching column
        declaration — the mirror of the schema totality check, one layer out.
        """
        spec = ENTITIES[entity]
        surface = self._surface(spec, layer)
        allowed = _declared_params(spec["mutable"]) | {
            name for (name, ell) in spec["extras"] if ell == layer
        }
        undeclared = sorted(surface - allowed)
        assert not undeclared, (
            f"{entity}: the {layer} surface takes arguments no column declares: "
            f"{undeclared}. Add each to the MUTABLE entry of the column it writes, or "
            f"to SURFACE_EXTRAS with the reason it is not a column write."
        )

    def test_every_declared_extra_really_exists(self, entity, layer):
        """SURFACE_EXTRAS cannot be padded with names that are not there."""
        spec = ENTITIES[entity]
        surface = self._surface(spec, layer)
        for (name, ell), reason in spec["extras"].items():
            if ell != layer:
                continue
            assert name in surface, (
                f"{entity}: SURFACE_EXTRAS declares {name!r} on the {layer} surface, "
                f"but it is not there."
            )
            assert len(reason.strip()) >= MIN_REASON_LEN, (
                f"{entity}: the {layer} extra {name!r} has no real reason: {reason!r}"
            )
