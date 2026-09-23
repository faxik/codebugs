"""CB-331: an agent seeing only descriptions should know WHEN to reach for a
low-traffic tool family, not just WHAT it does.

The curator's 2026-09-23 measurement was that self-directed calls into
`codesweep_*`, `codemerge_*`, `codebench_*`, `milestone_*`, `pull_next`,
`wip_status`, the embedding vector tools, `blockers_check`, `claims_held_by`
and `relations_unrelate` were rare and almost always prompted rather than
chosen — because every description said WHAT the tool does and none said in
what SITUATION an agent should reach for it. T-148 added a "Use when ..."
situation sentence to each; this file is the gate that keeps it there.

The perimeter is read from the LIVE registry (`tests._mcp_schema.collect_tool_schemas`,
the same production registration path `server._build_server` uses), never
hardcoded as a name list — a family is whatever currently starts with its
prefix, so a tool added to one of these families later inherits the gate
automatically, and a renamed or removed single tool is caught by a premise
check instead of silently vanishing from what this test covers.
"""

from __future__ import annotations

import functools

from tests._mcp_schema import collect_tool_schemas

# Prefix families, and the LOWER BOUND this test was written against (CB-331,
# perimeter measured on main @ 53b79ec: 9 codesweep_*, 9 codemerge_*, 4
# codebench_*, 11 milestone_*). A family growing is fine and expected; a
# family SHRINKING below this bound means tools disappeared and the
# perimeter this gate protects quietly narrowed with them.
PREFIX_FAMILIES: dict[str, int] = {
    "codesweep_": 9,
    "codemerge_": 9,
    "codebench_": 4,
    "milestone_": 11,
}

# Single tools outside any prefix family. Each name must be a LIVE tool, or
# the whole declared set has rotted silently (a rename or removal) rather
# than being caught here.
SINGLE_TOOLS: tuple[str, ...] = (
    "pull_next",
    "wip_status",
    "reqs_embed",
    "reqs_batch_embed",
    "reqs_search_similar",
    "reqs_embedding_stats",
    "blockers_check",
    "claims_held_by",
    "relations_unrelate",
)

# Declared pairs / chains (CB-331 brief §2): the FIRST tool's description
# must name the SECOND by its exact live tool name, so an agent reading only
# the first side still has a route to its counterpart. Not every perimeter
# tool has a declared partner -- only the ones the brief calls out as
# paired or reciprocal operations.
DECLARED_PAIRS: tuple[tuple[str, str], ...] = (
    ("codesweep_next", "codesweep_mark"),
    ("codemerge_start", "codemerge_claim"),
    ("codemerge_claim", "codemerge_check"),
    ("codemerge_check", "codemerge_merge"),
    ("codemerge_merge", "codemerge_finish"),
    ("codemerge_abandon", "codemerge_merge"),
    ("pull_next", "release_item"),
    ("reqs_embed", "reqs_search_similar"),
    ("reqs_search_similar", "reqs_embed"),
    ("relations_unrelate", "relations_relate"),
)


@functools.cache
def _catalog() -> dict[str, dict]:
    """Live tool name -> its schema dict ({name, description, inputSchema})."""
    return {tool["name"]: tool for tool in collect_tool_schemas()}


def _family_names(catalog: dict[str, dict], prefix: str) -> list[str]:
    return sorted(name for name in catalog if name.startswith(prefix))


class TestPerimeterPremises:
    """The perimeter itself must still exist before its contents are judged.

    These are premise checks, not the feature under test: if a family went
    empty or a declared single tool vanished, every assertion below would
    either pass vacuously or raise KeyError with no useful message. Failing
    loudly here, first, is what keeps that from reading as "all clear".
    """

    def test_prefix_families_are_nonempty_and_at_least_their_recorded_size(self):
        catalog = _catalog()
        assert catalog, "premise: the live tool catalogue is empty"
        for prefix, expected_minimum in PREFIX_FAMILIES.items():
            names = _family_names(catalog, prefix)
            assert names, f"premise: family {prefix!r} has no live tools at all"
            assert len(names) >= expected_minimum, (
                f"family {prefix!r} has {len(names)} live tool(s), fewer than "
                f"the {expected_minimum} this gate was written against -- the "
                "perimeter shrank (a tool was renamed or removed) without "
                "this test being updated to match"
            )

    def test_declared_single_tools_are_all_live(self):
        catalog = _catalog()
        missing = sorted(name for name in SINGLE_TOOLS if name not in catalog)
        assert not missing, (
            f"declared single tool(s) {missing} do not exist in the live "
            "catalogue -- SINGLE_TOOLS rotted (a rename or removal happened "
            "with nobody updating this list)"
        )

    def test_declared_pair_names_are_all_live(self):
        catalog = _catalog()
        named = {name for pair in DECLARED_PAIRS for name in pair}
        missing = sorted(name for name in named if name not in catalog)
        assert not missing, (
            f"DECLARED_PAIRS names {missing}, which do not exist in the live "
            "catalogue -- a pair rotted"
        )


def _perimeter_tools(catalog: dict[str, dict]) -> dict[str, dict]:
    names: set[str] = set(SINGLE_TOOLS)
    for prefix in PREFIX_FAMILIES:
        names.update(_family_names(catalog, prefix))
    return {name: catalog[name] for name in names}


class TestEveryPerimeterToolNamesItsSituation:
    """(a) from the brief: every perimeter tool's description carries `Use when`."""

    def test_use_when_present_in_every_perimeter_description(self):
        catalog = _catalog()
        tools = _perimeter_tools(catalog)
        assert tools, "premise: the perimeter resolved to zero tools"
        missing = sorted(
            name for name, tool in tools.items() if "Use when" not in tool["description"]
        )
        assert not missing, (
            f"perimeter tool(s) {missing} carry no `Use when ...` situation "
            "sentence in their description -- CB-331's whole point is that "
            "an agent reading only the description can tell WHEN to reach "
            "for a low-traffic tool, not just what it does"
        )


class TestDeclaredPairsNameEachOther:
    """(c) from the brief: a declared pair's first side names its counterpart."""

    def test_first_side_names_the_second_by_exact_tool_name(self):
        catalog = _catalog()
        failures = []
        for first, second in DECLARED_PAIRS:
            description = catalog[first]["description"]
            if second not in description:
                failures.append((first, second))
        assert not failures, (
            "declared pair(s) where the first tool's description never "
            f"names the second by its live tool name: {failures} -- an agent "
            "reading only the first tool's description has no route to its "
            "declared counterpart"
        )
