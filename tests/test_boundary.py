"""Boundary tests for the findings/provenance/db.py split.

Verifies:
1. Post-add hook fires inside the same transaction as findings.add_finding (one commit).
2. findings.batch_add_findings fires hooks per row, then exactly one commit.
3. The canonical MCP tool surface matches the golden snapshot, on any supported interpreter.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import re

import pytest

from codebugs import db, findings
from tests._mcp_schema import collect_tool_schemas, dedent_docstring, normalize_description

# ---------------------------------------------------------------------------
# Declared exceptions to CB-156's render gate -- SELF-DELETING (CB-165).
# ---------------------------------------------------------------------------
#
# A row here is a PROMISE: at one named place the gate below is deliberately not
# applied, because the normalizer looked at that section and was right to leave
# it indented. A promise stops being true the moment the special case goes away,
# and from that minute the row exempts something that WOULD have passed --
# silently, and for good. That is why this is a table with two tests at it and
# not the bare `{("codesweep_add", "Returns:")}` set it replaces: nothing asked
# whether the tool still existed, whether it still carried a section of that
# shape, or whether the normalizer was still the reason it did.
#
# The pattern is this tree's own, from `tests/test_strict_bool_gates.py`
# (`DECLARED_EXCEPTIONS` plus `test_every_declared_exception_carries_a_non_empty
# _reason` and `test_every_declared_exception_still_names_a_real_non_strict_bool
# _param`), and the rule it states is the one that applies here verbatim: "once
# a direction fixes its parameter, the row must be REMOVED, not left to rot."
# The repository states the other half for `SURFACE_GAPS`: "a hole declared for
# an argument that is in fact present fails the gate too, so the list cannot rot
# into permission to skip a surface."
#
# The two tests close DIFFERENT refusals and neither implies the other: a row
# can name a live section and carry no reason, and a reasoned row can name a
# section that vanished last month.
PROSE_SECTIONS_LEFT_ALONE: dict[tuple[str, str], str] = {
    ("codesweep_add", "Returns:"): (
        "CB-156: the body is a single brace-set phrase -- "
        "`{sweep_id, added, recurrence_bumped, duplicates_skipped (alias)}` -- "
        "and not a `name: value` item list, so `server._fold_section_items` "
        "declines to fold it and `markdown_sections` leaves the section "
        "byte-identical by its own documented rule. Converting it anyway would "
        "invent a list where the author wrote one phrase, which is the gate "
        "exceeding its remit rather than the author owing it a fix."
    ),
}


def _lazily_continued_sections(description: str) -> set[str]:
    """The section headers in `description` that a client renders as run-on prose.

    ONE definition, used by the gate and by the table's own self-deletion test
    (CB-165). Two copies of this shape would be one drift away from the gate
    exempting a section the allowlist test believes it is judging -- the exact
    "sharing an implementation does not share a decision" failure this
    repository keeps relearning.

    THE SHAPE, and why it is the render claim: a Google-style header at column 0
    whose next line is INDENTED is what CommonMark reads as a paragraph followed
    by lazy continuations -- the indentation is stripped, softbreaks become
    spaces, and the body fuses into one line. No CommonMark parser is a
    dependency here (this repository already refused adding one for an
    assertion), so the structural property stands in for the render.

    Measured BY LINES, which is the method that gives the right answer: splitting
    on the `Args:` TOKEN instead makes the empty tail of the `Args:` line look
    like a blank line and reports 73 indented CODE BLOCKS where the real count
    is 0.
    """
    lines = description.split("\n")
    return {
        line.strip()
        for i, line in enumerate(lines[:-1])
        if re.fullmatch(r"([A-Za-z][A-Za-z ]*):[ \t]*", line)
        and lines[i + 1].strip()
        and lines[i + 1].startswith("    ")
    }


class CountingConn:
    """sqlite3.Connection proxy that counts commits THROUGH BOTH SEAMS.

    (sqlite3.Connection.commit is C-implemented and read-only, so we can't
    monkeypatch it directly.)

    Both seams, per CLAUDE.md testing rule (c): plain code commits via
    ``conn.commit()``; ``db.txn`` commits via ``conn.execute("COMMIT")``. The
    original counted only the first, so moving add_finding into ``db.txn`` made
    its count silently drop to 0 — the single-commit guarantee pinned by a test
    that could not fail. ``__setattr__`` must forward too: ``db.txn`` assigns
    ``conn.isolation_level``, and without forwarding that lands on the proxy's
    ``__dict__`` while the real connection keeps its old mode.
    """

    _OWN = ("_conn", "commit_count")

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "commit_count", 0)

    def commit(self):
        object.__setattr__(self, "commit_count", self.commit_count + 1)
        return self._conn.commit()

    def execute(self, sql, *args):
        if isinstance(sql, str) and sql.strip().rstrip(";").upper() == "COMMIT":
            object.__setattr__(self, "commit_count", self.commit_count + 1)
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


@pytest.fixture
def conn(tmp_path):
    """Fresh DB on disk so connect()'s schema-init path runs end-to-end."""
    db.init_project(str(tmp_path))
    c = db.connect(str(tmp_path))
    yield c
    c.close()


class TestPostAddHookAtomicity:
    """Hard constraint #1: hooks fire inside the same transaction as the INSERT."""

    def test_add_finding_runs_hook_before_commit(self, conn):
        """The hook must observe the inserted row but run BEFORE commit returns."""
        seen = []

        def hook(c, finding):
            row = c.execute("SELECT id FROM findings WHERE id = ?", (finding["id"],)).fetchone()
            seen.append((finding["id"], row is not None))

        db.register_post_add_hook("test.atomicity_hook", hook)
        try:
            result = findings.add_finding(
                conn,
                severity="high",
                category="bug",
                file="a.py",
                description="d", new_category=True,
            )
        finally:
            db._post_add_hooks[:] = [
                h for h in db._post_add_hooks if h.name != "test.atomicity_hook"
            ]

        assert seen == [(result["id"], True)]

    def test_add_finding_commits_exactly_once(self, conn):
        """add_finding should call conn.commit() exactly once per finding."""
        proxy = CountingConn(conn)
        findings.add_finding(
            proxy,
            severity="low",
            category="x",
            file="a.py",
            description="d", new_category=True,
        )
        assert proxy.commit_count == 1

    def test_batch_add_findings_fires_hook_per_row_then_one_commit(self, conn):
        """Hard constraint: one logical transaction — N inserted members fire N
        hooks and the whole batch commits exactly ONCE. (Members here have three
        distinct descriptions, so all three genuinely insert; a deduplicated
        member fires no hook by design.)"""
        hook_calls: list[str] = []

        def hook(c, finding):
            hook_calls.append(finding["id"])

        db.register_post_add_hook("test.batch_hook", hook)
        proxy = CountingConn(conn)
        try:
            results = findings.batch_add_findings(
                proxy,
                [
                    {"severity": "high", "category": "bug", "file": "a.py", "description": "d1"},
                    {
                        "severity": "medium",
                        "category": "style",
                        "file": "b.py",
                        "description": "d2",
                    },
                    {"severity": "low", "category": "perf", "file": "c.py", "description": "d3"},
                ], new_category=True,
            )
        finally:
            db._post_add_hooks[:] = [h for h in db._post_add_hooks if h.name != "test.batch_hook"]

        ids = [r["id"] for r in results]
        assert hook_calls == ids, "hook should fire once per row, in insertion order"
        assert proxy.commit_count == 1, (
            "batch_add_findings must commit exactly ONCE for the whole batch"
        )


class TestMcpWireSchema:
    """Regression gate: the CANONICAL MCP tool surface must not drift unintentionally.

    Canonical, not byte-exact-wire: docstring indentation is normalized away,
    because CPython 3.13 dedents docstrings at compile time and 3.11/3.12 do not,
    while `requires-python` admits all three. Everything else — names, schemas,
    and every other whitespace difference including boundary blank lines and tabs
    — is still compared exactly (CB-70). What a 3.12-hosted server actually emits
    on the wire therefore still differs cosmetically; that is CB-73.
    """

    GOLDEN = pathlib.Path(__file__).parent / "golden" / "mcp_schema.json"

    def test_schema_matches_golden(self):
        """Tool surface (names + inputSchema + descriptions) must match the golden.

        If this fails: either (a) you intentionally changed a tool — regenerate the golden
        with `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`,
        or (b) you accidentally drifted — fix the offending change.

        DECLARED BLIND SPOT (CB-147): this gate does not, and cannot, catch a reordering of
        `inputSchema.properties` keys within one tool. `tests/dump_schema.py` writes the golden
        with `json.dumps(..., sort_keys=True)`, which re-sorts every object's keys alphabetically
        AT WRITE TIME — measured on the live golden: `codesweep_create`'s `properties` read
        `default_batch_size, description, lifecycle, name, terminal_states, transitions`, while
        `sweep.create_sweep`'s signature declares them `name, description, default_batch_size,
        lifecycle, terminal_states, transitions`. The declared order is simply not present in the
        file this test compares against, so no comparison strategy over that file could recover it
        (a prior card proposed comparing the golden as raw JSON text instead of parsed dicts; that
        would still miss this, because the text itself is already alphabetized).

        This is a decision, not an oversight: (1) a JSON object's member order is not a guaranteed
        part of the wire contract — RFC 8259 leaves it unordered, and a client is free to render
        `properties` in any order, including its own sort; (2) MCP tool calls bind arguments BY
        NAME, so no client BEHAVIOR depends on this order; (3) `sort_keys=True` gives a stable
        diffable golden, and undoing it to chase this ordering would buy a property the protocol
        does not promise at the cost of a full golden regeneration and a permanent new dependency
        on declaration order. (Reason (3) is an inference about intent, not a recorded decision:
        `dump_schema.py`'s own docstring says "flat sorted list", which is about the ORDER OF THE
        TOOL LIST, and nowhere states that sorting the KEYS was deliberate.)

        WHAT THIS DOES NOT CLAIM, corrected after the T-53 acceptance measured it. An earlier
        draft of this docstring said the only thing a reordering touches is the positional
        signature of the generated callable, "which nothing outside this repository's own tests
        can see". That is FALSE, and the measurement is one line: the SERVER serves `properties`
        in DECLARATION order (`name, description, default_batch_size, ...`) — the alphabetisation
        exists only in the written golden. So a client that renders the schema in the order it
        receives DOES see a reordering, which is the user-visible cost CB-147 itself named (the
        CB-73 class). Reason (1) still carries the decision — a JSON object's member order is not
        part of the contract, so a client relying on it relies on something unpromised — but the
        word "only" was an overclaim, and it is withdrawn rather than defended.

        Contrast with the CLI arg-order snapshot (T-51): there, order IS pinned, because it is
        semantically load-bearing — positional CLI arguments are parsed by position and `--help`
        renders them in the declared order. Neither of those is true of a JSON object's keys, so
        treating the two surfaces identically would be the mistake, not the fix.

        See `test_golden_properties_are_alphabetically_sorted` below, which pins the declaration
        above against the artifact rather than leaving it as prose: it asserts the golden really
        IS key-sorted, so a change that silently stopped sorting (e.g. dropping `sort_keys=True`
        thinking that "fixes" the blind spot) turns that test red instead of leaving this docstring
        quietly wrong.
        """
        assert self.GOLDEN.exists(), (
            f"Golden file missing at {self.GOLDEN}. Regenerate with the dump script."
        )
        expected = json.loads(self.GOLDEN.read_text())
        current = collect_tool_schemas()

        if current != expected:
            cur_names = {t["name"] for t in current}
            exp_names = {t["name"] for t in expected}
            added = sorted(cur_names - exp_names)
            removed = sorted(exp_names - cur_names)
            drifted = sorted(
                t["name"]
                for t in current
                if t["name"] in exp_names
                and t != next(e for e in expected if e["name"] == t["name"])
            )
            detail = ""
            if drifted:
                # Name the field that actually differs. Without this the reader
                # gets 64 tool names and no way to tell a real surface change
                # from whitespace, which is what made regenerating look right.
                cur = next(t for t in current if t["name"] == drifted[0])
                exp = next(e for e in expected if e["name"] == drifted[0])
                fields = [k for k in cur if cur[k] != exp.get(k)]
                detail = f"  First drift: {drifted[0]} differs in {fields}\n"
                if fields:
                    # Guarded rather than indexed blindly: this runs on the
                    # failure path, where an IndexError would replace the real
                    # diagnosis with a traceback about the diagnosis.
                    golden_val = repr(exp.get(fields[0]))
                    current_val = repr(cur[fields[0]])
                    # Show the window AROUND the first difference, not the first
                    # N characters. These strings share long prefixes, so a
                    # prefix excerpt prints the same text twice and tells the
                    # reader nothing — which is the failure this detail exists
                    # to prevent.
                    at = next(
                        (i for i, (a, b) in enumerate(zip(golden_val, current_val)) if a != b),
                        min(len(golden_val), len(current_val)),
                    )
                    window = slice(max(0, at - 40), at + 60)
                    detail += (
                        f"    first differing character at offset {at}\n"
                        f"    golden:  …{golden_val[window]}…\n"
                        f"    current: …{current_val[window]}…\n"
                    )
            pytest.fail(
                f"MCP schema drift detected.\n"
                f"  Added tools: {added}\n"
                f"  Removed tools: {removed}\n"
                f"  Drifted tools: {drifted}\n"
                f"{detail}"
                f"If intentional, regenerate with:\n"
                f"  PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json"
            )

    def test_golden_is_already_normalized(self):
        """The golden must be a FIXED POINT of the normalizer, on any interpreter.

        This is the assertion that closes the harm CB-70 was filed about. Fixing
        the generator and the comparator does not stop a golden that was
        hand-edited, merged badly, or produced by an older script from sitting in
        the un-normalized form — and the gate would then fail with the same
        unhelpful "64 drifted" and the same temptation to regenerate. Unlike
        running the gate under 3.12, this check needs no second interpreter, which
        matters because there is no CI to run one (CB-59).
        """
        for entry in json.loads(self.GOLDEN.read_text()):
            assert dedent_docstring(entry["description"]) == entry["description"], (
                f"{entry['name']}'s golden description is not normalized — the golden was "
                f"probably regenerated on Python < 3.13. Regenerate it on any interpreter "
                f"with the current dump script."
            )

    def test_no_golden_description_reaches_the_client_as_a_lazy_continuation(self):
        """STRUCTURAL gate on CB-156, and the property is what makes it a RENDER claim.

        The text gate above says "the string changed"; the question this card asks is
        "does a human read it as a list". No CommonMark parser is a dependency here and
        one is deliberately not added for an assertion (this repository already refused
        that for PyYAML), so the assertion is instead the STRUCTURAL property the render
        follows from: a Google-style section header at column 0 whose next line is
        INDENTED is exactly the shape CommonMark reads as a paragraph plus lazy
        continuations — the indentation is stripped, softbreaks become spaces, and every
        argument fuses into one run-on line. If no description carries that shape, no
        argument can arrive as a lazy continuation, which is the render property.

        Measured over this golden BY LINES, before the fix: 74 of 83 descriptions carried
        the shape (`Args:` 73, `Returns:` 3, two descriptions carrying both). After: 1 —
        `codesweep_add`'s `Returns:`, whose body is a single prose line rather than a
        `name: value` list, so it is deliberately left alone and is allowlisted BY NAME
        here rather than by a shape rule, so a second one cannot join it quietly.

        Note the measurement method, because the wrong one gives a confident wrong answer:
        split the description into LINES. Splitting on the `Args:` TOKEN instead makes the
        empty tail of the `Args:` line itself look like a blank line, which reports 73
        indented CODE BLOCKS where the real count is 0.
        """
        offenders = [
            f"{entry['name']}: {header}"
            for entry in json.loads(self.GOLDEN.read_text())
            for header in sorted(_lazily_continued_sections(entry["description"]))
            if (entry["name"], header) not in PROSE_SECTIONS_LEFT_ALONE
        ]
        assert not offenders, (
            "these sections still reach the client as a paragraph with lazy continuations "
            f"instead of a Markdown list (CB-156): {offenders}"
        )

    def test_every_prose_exception_carries_a_non_empty_reason(self):
        """Half one of CB-165. A table that can grow silently is the same hole
        the gate above exists to close, one level up — a row with no reason is
        indistinguishable from a row somebody added to make a red test green.
        """
        empty = [key for key, reason in PROSE_SECTIONS_LEFT_ALONE.items() if not reason.strip()]
        assert not empty, (
            f"PROSE_SECTIONS_LEFT_ALONE row(s) with no reason: {empty} — a reason names who "
            "decided and why, and without one the row is just permission."
        )

    def test_every_prose_exception_still_names_a_section_the_normalizer_left_alone(self):
        """Half two of CB-165, and the half nothing held: the table must SHRINK.

        An allowlist row is only honest while its special case exists. A tool that
        was renamed away, a docstring whose `Returns:` grew into a real `name:
        value` list, a section deleted outright — each leaves a row that goes on
        exempting whatever later takes that name, silently and permanently. So a
        stale row is refused exactly as a missing reason is.

        THREE conditions, and each one refuses a different way for the row to
        have rotted:

        1. The tool still exists on the surface.
        2. The section is still in the offender shape. A row that exempts nothing
           frees nothing and must go — this is `SURFACE_GAPS`' rule verbatim: a
           hole declared for something that is in fact fine fails the gate too.
        3. The NORMALIZER is what left it indented. This is the condition that
           makes the row a decision rather than an accident: `markdown_sections`
           documents that it leaves a section byte-identical "whenever it is not
           an item list", so re-running it and finding the section still indented
           is the production code itself certifying the exemption. The check
           reads the golden's own text, so it does not duplicate the normalizer's
           predicate and cannot drift from it.

           BE HONEST ABOUT WHEN CONDITION 3 CAN ACTUALLY FIRE, because the first
           draft of this docstring justified it with a scenario that cannot
           happen. It claimed the condition guards against a STALE golden — but
           `test_schema_matches_golden`, four methods up in this same class,
           already refuses that, and measured over today's file all 83 golden
           descriptions are fixed points of `normalize_description`, so condition
           3 follows from condition 2 on every row that exists right now.
           What makes it live is the state CB-164 newly admits: since the
           snapshot registers through the production adapter, a tool that passes
           its own `description=` reaches the golden UNNORMALIZED, and such a
           description IS in the offender shape while the normalizer WOULD have
           folded it (both measured). A row exempting that section would be
           excusing a defect the normalizer was ready to fix, which is the one
           thing an allowlist must never be allowed to do — so the row is refused
           and the author is sent to stop passing `description=` instead.
        """
        by_name = {e["name"]: e["description"] for e in json.loads(self.GOLDEN.read_text())}
        stale = []
        for tool, header in sorted(PROSE_SECTIONS_LEFT_ALONE):
            description = by_name.get(tool)
            if description is None:
                stale.append(f"{tool}: no tool of that name is on the surface any more")
            elif header not in _lazily_continued_sections(description):
                stale.append(
                    f"{tool}: {header} is no longer an indented section, so this row exempts "
                    f"nothing — remove it"
                )
            elif header not in _lazily_continued_sections(normalize_description(description)):
                stale.append(
                    f"{tool}: {header} survives in the golden but the normalizer would fold "
                    f"it — the indent is not its decision, so the exemption is not one either"
                )
        assert not stale, (
            f"PROSE_SECTIONS_LEFT_ALONE row(s) that have stopped being true: {stale}. This "
            "table may only SHRINK: once the special case goes away the row must be REMOVED, "
            "not left to rot into permission to skip a section."
        )

    def test_every_golden_section_body_is_a_markdown_list(self):
        """The other half, and neither test implies the other.

        The test above proves no section is still INDENTED; this one proves the sections
        that were converted actually became a LIST. A mutant that simply deleted the
        argument lines, or dedented them to column 0 without a marker, would satisfy the
        first assertion and produce prose again — so the marker is asserted directly.

        The header is matched as a SINGLE word here, unlike the test above, and that is
        not an oversight: this surface also carries ordinary prose sentences that end in
        a colon and introduce un-indented content (`Parses markdown tables with columns:`
        in `reqs_import`, and two more). Those are paragraphs, not Google-style sections,
        and demanding a bullet under them would be this gate inventing a rule. They stay
        covered by the test above, which judges by the INDENT of what follows and so
        classifies them correctly without needing to know their names.
        """
        expected = {"Args", "Returns"}
        seen, missing = set(), []
        for entry in json.loads(self.GOLDEN.read_text()):
            lines = entry["description"].split("\n")
            for i, line in enumerate(lines):
                header = re.fullmatch(r"([A-Za-z]+):[ \t]*", line)
                if not header:
                    continue
                body = [x for x in lines[i + 1 :] if x.strip()][:1]
                if not body or body[0].startswith("    "):
                    continue  # prose section body, covered by the test above
                seen.add(header.group(1))
                if not body[0].startswith("- "):
                    missing.append(f"{entry['name']}: {line.strip()} -> {body[0][:40]!r}")
        assert not missing, f"section bodies that are not Markdown list items: {missing}"
        assert seen >= expected, (
            f"this gate saw only {sorted(seen)} — if a section kind vanished from the "
            f"surface the assertion above became vacuous for it"
        )

    def test_golden_properties_are_alphabetically_sorted(self):
        """Pins the CB-147 declaration in `test_schema_matches_golden`'s docstring: the golden is
        key-sorted, which is WHY the gate above is blind to `properties` order and not merely
        asserted to be.

        Without this test the docstring and the file could drift apart silently — exactly this
        repo's recurring failure mode (a rule stated in prose stops matching the code that is
        supposed to embody it). Two ways that drift could happen, and this test catches both:
        someone removes `sort_keys=True` from `tests/dump_schema.py` thinking that is how you'd
        PIN order (it would instead make the golden's sortedness accidental and file-dependent,
        contradicting this test — the intended outcome, since undoing the sort is the opposite of
        the ratified decision and must surface as a red test, not a silent behavior change); or a
        golden gets hand-edited or produced by some other path that does not sort.

        Every tool is checked, `properties`-less ones included — an empty (or absent) `properties`
        dict has an empty key list, which is trivially its own sorted() and asserts True for a
        different, uninteresting reason (there is no order to lose). Counted explicitly so that
        does not read as false coverage: as of this writing 6 of 83 tools take no parameters
        (`blockers_check`, `categories`, `codemerge_status`, `reqs_embedding_stats`,
        `reqs_summary`, `summary`) and are vacuous for this assertion; the remaining 77 are where
        the check is actually live.
        """
        golden = json.loads(self.GOLDEN.read_text())
        assert len(golden) > 0, "golden is empty — nothing for this test to check"

        vacuous = []
        live = []
        for entry in golden:
            props = list(entry.get("inputSchema", {}).get("properties", {}).keys())
            if not props:
                vacuous.append(entry["name"])
                continue
            live.append(entry["name"])
            assert props == sorted(props), (
                f"{entry['name']}'s golden `properties` keys are not alphabetically sorted "
                f"({props}). Either the golden was hand-edited/regenerated without "
                f"`sort_keys=True`, or `tests/dump_schema.py` no longer sorts — in which case the "
                f"CB-147 blind-spot declaration in test_schema_matches_golden's docstring is now "
                f"WRONG and must be revisited, not just this test."
            )

        # This test is meaningful only if there really are tools with parameters to check.
        assert live, "no tool in the golden has any properties — the sortedness check is vacuous"

    def test_collector_normalizes_interpreter_dependent_indentation(self):
        """CB-70. On 3.11/3.12 `__doc__` keeps its source indentation; on 3.13 the
        compiler strips it. The golden was generated on 3.13, so before this the
        gate reported 64 of 68 tools as "drifted" on 3.12 — and told the reader to
        regenerate, which would have broken it for everyone on 3.13.

        The failure only reproduces on an interpreter this machine may not have, so
        the 3.12 condition is CONSTRUCTED instead: assigning __doc__ after the def
        bypasses the compiler, giving byte-identical input on every version.

        Two vacuity traps, both named by review:
        (1) assert the SDK still hands us the INDENTED text first — otherwise a
            future SDK that dedents on its own would make this pass while proving
            nothing;
        (2) the collector walks the provider registry, so a tool registered
            anywhere else would never be observed — hence the injected provider.

        Since CB-164 the collector does not normalize with its own hands: it
        registers through the production adapter and the adapter normalizes. What
        this test observes — the collector's OUTPUT is dedented — is unchanged and
        still the right thing to assert; only the mechanism behind it moved, and
        the test below pins the half that mechanism newly makes visible.
        """
        indented = "Summary line.\n\n    Indented body.\n    "

        def synthetic_tool(value: str) -> dict:
            return {"value": value}

        synthetic_tool.__doc__ = indented

        def register(mcp, conn_factory):
            mcp.tool()(synthetic_tool)

        provider = db.ToolProvider(name="cb70_probe", register_fn=register)

        raw = self._raw_description(provider)
        assert raw == indented, (
            "precondition failed: the SDK no longer passes __doc__ through verbatim, "
            "so this test can no longer prove the collector does the normalizing"
        )

        collected = collect_tool_schemas(providers=[provider])
        assert [t["name"] for t in collected] == ["synthetic_tool"]
        assert collected[0]["description"] == "Summary line.\n\nIndented body.\n"

    def test_a_tool_passing_its_own_description_lands_in_the_snapshot_unnormalized(self):
        """What CB-164's fix buys — and the only thing protecting the fix itself.

        The collector used to build a BARE server and apply `normalize_description`
        by hand, so the snapshot was a RECONSTRUCTION of the wire rather than a
        record of it. `src/codebugs/surfacegen.py` already named the hazard that
        follows, and guarded it with nothing but a convention: "A generated tool
        passing `description=` would therefore match the golden byte for byte and
        still ship un-dedented text to clients — CB-73 resurrected behind the very
        gate built to catch it."

        Registering through the production adapter closes it. An explicit
        `description=` WINS over `__doc__` by `_NormalizedDescriptions`' own
        documented rule — "a caller that passed one has already said what the
        client should see" — so the raw text now reaches the snapshot exactly as a
        client would receive it, and CB-156's render gate above names it. The last
        assertion is that composition, and it is why the first two are not enough
        on their own: verbatim text is only worth recording if the gate can then
        read it.

        THE REASON THIS TEST EXISTS AT ALL, measured rather than feared: with
        `tests/_mcp_schema.py` reverted to the hand-normalizing version,
        `test_boundary.py`, `test_server.py` and `test_loc.py` came to 193 passed.
        The golden cannot move, because no tool on today's surface passes
        `description=` — so the fix for "a gate that cannot fire" was itself a
        change nothing could catch being undone. Leaving that as a comment would
        have been this unit's own subject committed inside its own fix.
        """
        google = "Summary line.\n\nArgs:\n    severity: how bad it is\n    file: where\n"

        def synthetic_tool(value: str) -> dict:
            return {"value": value}

        def register(mcp, conn_factory):
            # No docstring on the function: `description=` is the whole input, so
            # what comes back can only be the adapter's verdict on it.
            mcp.tool(description=google)(synthetic_tool)

        provider = db.ToolProvider(name="cb164_probe", register_fn=register)
        collected = collect_tool_schemas(providers=[provider])
        assert [t["name"] for t in collected] == ["synthetic_tool"]
        assert collected[0]["description"] == google, (
            "the collector normalized a description its caller supplied — it is "
            "reconstructing the wire again instead of recording it (CB-164)"
        )
        assert "Args:" in _lazily_continued_sections(collected[0]["description"]), (
            "the snapshot recorded the text verbatim but CB-156's gate cannot see "
            "the section in it, so recording it bought nothing"
        )

    @staticmethod
    def _raw_description(provider) -> str:
        """What the SDK reports before the collector normalizes anything."""
        from mcp.server.mcpserver import MCPServer

        async def go():
            server = MCPServer(provider.name)
            provider.register_fn(server, None)
            return (await server.list_tools())[0].description

        return asyncio.run(go())

    def test_dedent_is_a_noop_on_already_dedented_docstrings(self):
        """It must not touch 3.13 output, or it would rewrite the golden it exists
        to leave alone. Verified across all 68 real tools when this landed (0
        changed); pinned here on the shapes that make it non-trivial — note the
        last one keeps its RELATIVE indentation, which is what distinguishes this
        from inspect.cleandoc."""
        for already_clean in (
            "Summary line.\n\nBody.\n",
            "One-liner.",
            "Summary.\n\nBody.\n\n",
            "Summary.\n\nTop level.\n    Nested under it.\n",
        ):
            assert dedent_docstring(already_clean) == already_clean, already_clean

    def test_dedent_reproduces_the_compiler_on_awkward_indentation(self):
        """Each expectation here was MEASURED against the real CPython 3.13
        compiler, not derived from reading it: the same source was compiled on
        3.12 and 3.13, and normalizing 3.12's `__doc__` reproduced 3.13's exactly
        for every case. Two are counter-intuitive and were guessed wrong first —
        a tab counts as one column, so a tab-indented body IS dedented, and a
        whitespace-only line does not lower the computed margin."""
        # A tab-indented body: the margin is one column, so the tab goes.
        assert dedent_docstring("Summary.\n\n\tBody.\n\t") == "Summary.\n\nBody.\n"
        # A whitespace-only line is ignored when computing the margin, so the
        # body's own indentation decides it.
        assert dedent_docstring("Summary.\n   \n    Body.\n    ") == "Summary.\n\nBody.\n"
        # The closing-quote line is shallower than the body; the body still wins.
        assert dedent_docstring("Summary.\n\n        Deep.\n    ") == "Summary.\n\nDeep.\n"


class TestBt4FreshnessDeclarations:
    """BT-4 (ratified 2026-08-20): `source`, top-level `meta` and
    `reported_at_ref` are observation-frozen — a dedup bump never updates the
    column; later observations' values live only in the occurrence ring. T-11
    declares that freeze on the MCP readers, and these pins tie the declared
    prose to the executable fact it describes (the CB-114 docstring-pin shape:
    regex-extract the claim, compare with the code), so neither can silently
    drift from the other. The behavioural side of the freeze is pinned in
    `tests/test_dedup.py::TestObservationFrozenFields`.
    """

    @pytest.fixture(scope="class")
    def descriptions(self):
        providers = [p for p in db.get_tool_providers(mode="all") if p.name == "findings"]
        assert providers, "the findings tool provider vanished from the registry"
        return {t["name"]: t["description"] for t in collect_tool_schemas(providers)}

    @staticmethod
    def _args_entry(description: str, param: str) -> str:
        """The Args-block entry for `param`: its own line plus continuations.

        A continuation is any following line that neither starts a new
        `name:` entry nor is blank. Joined to one string so the assertions
        below are indifferent to where the prose wraps.

        BOTH SPELLINGS ARE ACCEPTED, and the reason is worth a line. Since
        CB-156 an argument reaches the client as a Markdown list item
        (`- name: text`) with its wrapped lines already folded into it, so the
        continuation loop below is a no-op on the live surface. It is kept
        rather than deleted because this helper describes a SHAPE, and the
        indented Google form is still what the docstrings themselves carry —
        a caller feeding this a raw `__doc__` must get the same answer as one
        feeding it the normalized wire text.
        """
        lines = description.splitlines()
        entry_start = re.compile(rf"\s*(?:[-*+] )?{param}:")
        any_entry = re.compile(r"\s*(?:[-*+] )?\w+:")
        starts = [i for i, ln in enumerate(lines) if entry_start.match(ln)]
        assert starts, f"no Args entry for {param!r}"
        entry = [lines[starts[0]]]
        for ln in lines[starts[0] + 1 :]:
            if not ln.strip() or any_entry.match(ln):
                break
            entry.append(ln)
        return " ".join(s.strip() for s in entry)

    def test_add_source_declares_the_first_reporter_freeze(self, descriptions):
        """The prose half is T-11's edit; the executable half already holds:
        the bump SET builder never assigns the column, and the ring entry
        carries the observation's source instead."""
        entry = self._args_entry(descriptions["add"], "source").lower()
        assert "first report" in entry, entry  # "first reporter" / "first report"
        assert "ring" in entry, entry
        assert "source = ?" not in inspect.getsource(findings._bump_row), (
            "the bump SET builder now writes `source` — the declared freeze is a lie"
        )
        assert "source" in inspect.signature(findings._occurrence_entry).parameters

    def test_query_ref_declares_exact_match_on_the_assigned_or_first_observed_ref(
        self, descriptions
    ):
        """The prose half is T-11's edit; the executable half already holds:
        the filter is equality on the column — never LIKE (unlike `commit`,
        which is documented prefix), and never a ring reader."""
        entry = self._args_entry(descriptions["query"], "ref").lower()
        assert "exact" in entry, entry
        assert "assigned" in entry, entry  # "...first-observed or manually assigned..."
        src = inspect.getsource(findings.query_findings)
        assert '"reported_at_ref = ?"' in src, "the ref filter is no longer plain equality"
        assert "reported_at_ref LIKE" not in src, "the ref filter must not become a prefix match"
