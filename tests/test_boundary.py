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
from tests._mcp_schema import collect_tool_schemas, dedent_docstring


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
        """
        lines = description.splitlines()
        starts = [i for i, ln in enumerate(lines) if re.match(rf"\s*{param}:", ln)]
        assert starts, f"no Args entry for {param!r}"
        entry = [lines[starts[0]]]
        for ln in lines[starts[0] + 1 :]:
            if not ln.strip() or re.match(r"\s*\w+:", ln):
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
