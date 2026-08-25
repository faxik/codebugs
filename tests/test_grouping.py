"""Grouping extension: citation graph, tag pivots, split lineage.

The three axes that carve a backlog into work units when similarity cannot. The
bugs live in the graph logic — hub splitting, lineage traversal — not in the
counting, so that is where the weight of this file sits.
"""

import sqlite3

import pytest

from codebugs import findings, grouping, relations


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


def add(conn, description, **kw):
    kw.setdefault("severity", "medium")
    kw.setdefault("category", "correctness")
    kw.setdefault("file", "a.py")
    return findings.add_finding(conn, description=description, **kw, new_category=True)["id"]


class TestExtraction:
    def test_extract_citations_is_ordered_and_deduped(self):
        assert grouping.extract_citations(
            "CB-9 relates to CB-4, and CB-9 again"
        ) == ["CB-9", "CB-4"]

    def test_extract_citations_needs_word_boundaries(self):
        # `XCB-4` and `CB-4a` are not references; `(CB-4)` is.
        assert grouping.extract_citations("XCB-4 CB-4a") == []
        assert grouping.extract_citations("see (CB-4).") == ["CB-4"]

    def test_extract_citations_tolerates_empty(self):
        assert grouping.extract_citations("") == []

    def test_context_quotes_the_first_mention_and_collapses_whitespace(self):
        text = "line one mentions CB-7\nand   the second line"
        got = grouping.citation_context(text, "CB-7", width=12)
        assert "CB-7" in got
        assert "\n" not in got and "   " not in got
        assert got.startswith("…")

    def test_context_is_empty_when_the_target_is_absent(self):
        assert grouping.citation_context("nothing here", "CB-7") == ""

    def test_normalize_label_folds_punctuation_and_case(self):
        assert (grouping.normalize_label("process_improvement")
                == grouping.normalize_label("Process-Improvement"))
        assert grouping.normalize_label("n-plus-one") != grouping.normalize_label("nplus2")


class TestCitationComponents:
    def test_chain_is_one_component_with_explained_edges(self, conn):
        a = add(conn, "first card, the origin of this thread")
        b = add(conn, f"second card, follows on from {a} directly")
        c = add(conn, f"third card, continues the work started in {b}")
        rep = grouping.citation_report(conn)
        assert rep["components_total"] == 1
        comp = rep["components"][0]
        assert {m["id"] for m in comp["members"]} == {a, b, c}
        assert comp["edge_count"] == 2
        for edge in comp["edges"]:
            assert edge["mentions"], "every edge must carry its mentions"
            for mention in edge["mentions"]:
                assert mention["field"] == "description"
                assert mention["dst"] in mention["context"]

    def test_citation_in_notes_counts_as_an_edge(self, conn):
        a = add(conn, "a card that will be referenced from another card's notes")
        b = add(conn, "an unrelated-looking card", meta={"notes": f"dup of {a}"})
        rep = grouping.citation_report(conn)
        assert rep["components_total"] == 1
        (edge,) = rep["components"][0]["edges"]
        assert {edge["a"], edge["b"]} == {a, b}
        assert edge["mentions"][0]["field"] == "meta.notes"

    def test_self_reference_is_counted_not_edged(self, conn):
        a = add(conn, "a card")
        findings.update_finding(conn, a, meta_update={"notes": f"{a} is this card"})
        rep = grouping.citation_report(conn)
        assert rep["self_references"] == 1
        assert rep["edges_total"] == 0
        assert rep["orphans_total"] == 1

    def test_all_three_counters_use_the_same_unit(self, conn):
        """citations / dangling / self-references sit on one header line, so they
        must count the same thing: one distinct (citing card, cited id) pair.
        Counting mentions per row and the other two per occurrence inflated
        dangling 3x on the reference corpus."""
        a = add(conn, "the in-population card that gets cited three times over")
        b = add(conn, f"cites {a} and the absent CB-9999, twice each: "
                      f"{a} again and CB-9999 again")
        findings.update_finding(conn, b, meta_update={
            "notes": f"{a} a third time, CB-9999 a third time, and itself {b}",
            "related": f"{a} a fourth time, CB-9999 a fourth time, {b} again",
        })
        rep = grouping.citation_report(conn)
        assert rep["citations_total"] == 1
        assert rep["dangling_total"] == 1
        assert rep["self_references"] == 1

    def test_reference_outside_the_population_is_dangling_not_dropped(self, conn):
        a = add(conn, "the card that gets fixed and leaves the live population")
        b = add(conn, f"a live card that still points at {a}")
        findings.update_finding(conn, a, status="fixed")
        rep = grouping.citation_report(conn)
        assert rep["rows_considered"] == 1
        assert rep["dangling_total"] == 1
        assert rep["components_total"] == 0
        # ... and widening the population restores the edge.
        wide = grouping.citation_report(conn, status="all")
        assert wide["dangling_total"] == 0
        assert {m["id"] for m in wide["components"][0]["members"]} == {a, b}

    def test_hub_does_not_transmit_connectivity(self, conn):
        hub = add(conn, "the landmark card everyone points at")
        left = [add(conn, f"left arm card {i} which cites {hub}") for i in range(3)]
        right = [add(conn, f"right arm card {i} which cites {hub}") for i in range(3)]
        # One extra edge inside each arm so the arms are components on their own.
        add(conn, f"left tail cites {left[0]} and nothing else")
        add(conn, f"right tail cites {right[0]} and nothing else")
        raw = grouping.citation_report(conn, hub_degree=None)
        assert raw["components_total"] == 1, "without hub splitting it is one blob"
        assert raw["components"][0]["size"] == 9

        split = grouping.citation_report(conn, hub_degree=4)
        assert split["hubs"] == [hub]
        assert split["components_total"] == 2
        assert sorted(c["size"] for c in split["components"]) == [2, 2]
        # The hub is not lost: it is named as the landmark each side hangs off.
        for comp in split["components"]:
            assert comp["hub_neighbours"] == [hub]

    def test_default_hub_degree_is_the_one_that_is_applied(self, conn):
        """Pins the DEFAULT, not just the parameter. Every other hub test passes
        hub_degree explicitly, so the shipped default was unexercised — and it is
        the value almost every caller actually gets."""
        hub = add(conn, "a card cited by exactly four others, one over the default")
        for i in range(4):
            add(conn, f"citer {i} of the four, which names {hub}")
        assert grouping.DEFAULT_HUB_DEGREE == 3
        assert grouping.citation_report(conn)["hubs"] == [hub]
        # One notch looser and the same card is an ordinary member again.
        assert grouping.citation_report(conn, hub_degree=4)["hubs"] == []

    def test_hub_is_reported_as_a_terminal_anchor_with_its_citers(self, conn):
        hub = add(conn, "the landmark card everyone points at")
        citers = [add(conn, f"card {i} which cites {hub} in passing") for i in range(3)]
        rep = grouping.citation_report(conn, hub_degree=2)
        (anchor,) = [a for a in rep["anchors"] if a["id"] == hub]
        assert anchor["in_degree"] == 3
        assert anchor["is_hub"] is True
        assert [c["id"] for c in anchor["citers"]] == sorted(citers)
        assert all(c["context"] for c in anchor["citers"])

    def test_anchors_need_two_citers(self, conn):
        a = add(conn, "a card cited exactly once by one other card")
        add(conn, f"the only card citing {a}")
        assert grouping.citation_report(conn)["anchors_total"] == 0

    def test_orphans_are_the_cards_with_no_link_at_all(self, conn):
        a = add(conn, "linked card one")
        add(conn, f"linked card two pointing at {a}")
        lonely = add(conn, "a card nobody mentions and which mentions nobody")
        rep = grouping.citation_report(conn)
        assert rep["orphans"] == [lonely]
        assert rep["orphans_total"] == 1

    def test_limits_page_members_and_their_edges_together(self, conn):
        a = add(conn, "first card of a four-card chain in this test")
        b = add(conn, f"second card of the chain, after {a}")
        c = add(conn, f"third card of the chain, after {b}")
        add(conn, f"fourth card of the chain, after {c}")
        rep = grouping.citation_report(conn, member_limit=2)
        comp = rep["components"][0]
        assert len(comp["members"]) == 2
        kept = {m["id"] for m in comp["members"]}
        assert all(e["a"] in kept and e["b"] in kept for e in comp["edges"])
        assert comp["size"] == 4, "size stays the family total so truncation is visible"

    def test_component_limit_does_not_change_the_totals(self, conn):
        for i in range(3):
            x = add(conn, f"pair {i} first card in this deliberately separate pair")
            add(conn, f"pair {i} second card, which points at {x}")
        rep = grouping.citation_report(conn, component_limit=1)
        assert len(rep["components"]) == 1
        assert rep["components_total"] == 3
        assert rep["cards_in_components"] == 6

    def test_rejects_negative_tuning(self, conn):
        with pytest.raises(ValueError, match="hub_degree"):
            grouping.citation_report(conn, hub_degree=-1)
        with pytest.raises(ValueError, match="member_limit"):
            grouping.citation_report(conn, member_limit=-1)

    def test_malformed_meta_degrades_to_no_citations(self, conn):
        a = add(conn, "a normal card that will be cited from a corrupt row")
        b = add(conn, f"a card whose meta is about to be corrupted, cites {a}")
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", ("not json", b))
        conn.commit()
        rep = grouping.citation_report(conn)
        # The description still cites; only the unreadable meta is skipped.
        assert rep["components_total"] == 1


class TestDistinctFromSuppression:
    """A DECLARED `distinct_from` beats an INFERRED citation edge (CB-62).

    Every fixture here creates its own suppressions. When this landed the live
    tracker held ZERO live `distinct_from` rows, so there is no corpus to lean
    on and nothing in this class is evidence about real data.
    """

    @pytest.fixture()
    def rel_conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        findings.ensure_schema(c)
        relations.ensure_schema(c)
        yield c
        c.close()

    @staticmethod
    def _cited_pair(conn):
        """CB-9 cites CB-10, with ids whose LEXICOGRAPHIC and NUMERIC orders
        disagree — which is what makes the canonicalisation observable.

        `relations._orient` canonicalises a symmetric pair with `src <= dst` on
        the STRING, and `citation_report` keys its edges with `sorted()`, the
        same order. `"CB-10" < "CB-9"` because `"1" < "9"`, so the canonical
        pair is ("CB-10", "CB-9") while reading the numbers says the opposite.
        A CB-1/CB-2 pair, where the two orders agree, would let an
        implementation that sorted by the NUMBER pass this whole class.
        """
        add(conn, "the card that the other one refers to", finding_id="CB-10")
        add(conn, "a different defect whose text mentions CB-10 in passing",
            finding_id="CB-9")
        assert sorted(["CB-9", "CB-10"]) == ["CB-10", "CB-9"], (
            "fixture invalid: these ids must make lexicographic and numeric "
            "order disagree, or the canonical order is never exercised"
        )
        return "CB-9", "CB-10"

    def test_a_citation_edge_alone_makes_one_component(self, rel_conn):
        """The BASELINE half of the oracle. Without it, the suppression test
        below is satisfied by any change that breaks grouping altogether."""
        self._cited_pair(rel_conn)
        rep = grouping.citation_report(rel_conn)
        assert rep["components_total"] == 1
        assert {m["id"] for m in rep["components"][0]["members"]} == {"CB-9", "CB-10"}
        assert rep["suppressed_total"] == 0

    def test_a_declared_distinct_from_keeps_the_cards_apart(self, rel_conn):
        src, dst = self._cited_pair(rel_conn)
        relations.relate(rel_conn, src, "distinct_from", dst, source="test")
        rep = grouping.citation_report(rel_conn)
        assert rep["components_total"] == 0
        # The EDGE survives; only the conclusion drawn from it is dropped.
        assert rep["edges_total"] == 1
        assert rep["citations_total"] == 1
        assert rep["suppressed_total"] == 1
        (sup,) = rep["suppressed_edges"]
        assert (sup["a"], sup["b"]) == ("CB-10", "CB-9")
        assert sup["mentions"][0]["src"] == "CB-9"
        assert "CB-10" in sup["mentions"][0]["context"], (
            "the quoted evidence must survive the suppression"
        )

    def test_a_retracted_suppression_stops_suppressing(self, rel_conn):
        """Reads the LIVE relation, not any relation. Passing
        `include_retracted=True` in `relations.active_suppressions` turns this
        red."""
        src, dst = self._cited_pair(rel_conn)
        relations.relate(rel_conn, src, "distinct_from", dst, source="test")
        relations.unrelate(rel_conn, src, "distinct_from", dst, retracted_by="test")
        rep = grouping.citation_report(rel_conn)
        assert rep["components_total"] == 1
        assert rep["suppressed_total"] == 0

    def test_the_declaration_holds_in_either_direction(self, rel_conn):
        """Declared the other way round, and it still suppresses.

        PINS PRESERVED BEHAVIOUR rather than catching a mutant, and says so
        because a reader cannot otherwise tell this from a broken test:
        `relate` canonicalises at write time, so this passes with or without
        the read-side `sorted()`. The test that discriminates that line is
        `test_a_non_canonically_stored_pair_still_suppresses`.
        """
        src, dst = self._cited_pair(rel_conn)
        relations.relate(rel_conn, dst, "distinct_from", src, source="test")
        assert grouping.citation_report(rel_conn)["components_total"] == 0

    def test_a_non_canonically_stored_pair_still_suppresses(self, rel_conn):
        """The READ side canonicalises too, so its correctness does not rest on
        the write side having done so.

        `relate` cannot produce this row today — it calls `_orient` before the
        INSERT — so the row is written directly. That is the point of the test,
        not a claim that the table lies: a reader whose correctness depends on
        another module's invariant is one unreviewed INSERT away from a
        suppression that silently does not fire. Deleting the `sorted()` from
        `relations.active_suppressions` turns THIS test red and leaves every
        other test in this class green.
        """
        src, dst = self._cited_pair(rel_conn)
        rel_conn.execute(
            "INSERT INTO finding_relations (src_id, rel, dst_id, created_at, source) "
            "VALUES (?, 'distinct_from', ?, '2026-01-01T00:00:00Z', 'test')",
            (src, dst),
        )
        rel_conn.commit()
        stored = rel_conn.execute(
            "SELECT src_id, dst_id FROM finding_relations"
        ).fetchone()
        assert (stored["src_id"], stored["dst_id"]) != tuple(sorted((src, dst))), (
            "fixture invalid: the row must be stored NON-canonically, or this "
            "test cannot exercise the read-side canonicalisation"
        )
        assert grouping.citation_report(rel_conn)["components_total"] == 0

    def test_a_third_card_citing_both_defeats_the_declaration_visibly(self, rel_conn):
        """THE HONEST SCOPE, pinned so the prose cannot drift away from it.

        Dropping one edge does not cut a graph. Declare A and B distinct while a
        third card cites both, and all three stay in one component — with the
        suppressed edge then listed BOTH in `suppressed_edges` and among that
        component's own edges. What makes that a limitation rather than a lie is
        that the report says so: `still_grouped` on the entry, counted by
        `still_grouped_total`. Two-node fixtures cannot see any of this, which is
        why the rest of this class is not enough on its own.
        """
        a = add(rel_conn, "the opening card of a chain that gets cross-referenced")
        b = add(rel_conn, f"a separate defect whose text happens to mention {a}")
        c = add(rel_conn, f"a triage note naming both {a} and {b} in one breath")
        relations.relate(rel_conn, a, "distinct_from", b, source="test")
        rep = grouping.citation_report(rel_conn)
        assert rep["suppressed_total"] == 1
        assert rep["still_grouped_total"] == 1
        (sup,) = rep["suppressed_edges"]
        assert sup["still_grouped"] is True
        assert rep["components_total"] == 1
        assert {m["id"] for m in rep["components"][0]["members"]} == {a, b, c}

    def test_a_declaration_on_a_hub_edge_is_still_reported(self, rel_conn):
        """The hub rule and the declaration are two different reasons not to
        join, and the ORDER they are asked in decides what the report can SAY.

        Asked hub-first, a declaration touching a landmark card never reaches
        the ledger comparison at all, so `suppressed_total` answers `0` with the
        declaration alive in the ledger — "no such channel" wearing the face of
        "looked, nothing fired". The grouping is identical either way, so only
        this assertion separates the two. Moving the suppression branch back
        below the hub branch turns this test red.
        """
        hub = add(rel_conn, "the landmark card that every neighbourhood points at")
        citers = [
            add(rel_conn, f"the alpha report, which points at {hub}"),
            add(rel_conn, f"the beta report, which also points at {hub}"),
            add(rel_conn, f"the gamma report, pointing at {hub} as well"),
            add(rel_conn, f"the delta report, likewise pointing at {hub}"),
        ]
        assert grouping.citation_report(rel_conn)["hubs"] == [hub], (
            "fixture invalid: the landmark must actually exceed hub_degree, or "
            "the hub branch is never the one that would have swallowed the edge"
        )
        relations.relate(rel_conn, hub, "distinct_from", citers[0], source="test")
        rep = grouping.citation_report(rel_conn)
        assert rep["suppressed_total"] == 1
        (sup,) = rep["suppressed_edges"]
        assert {sup["a"], sup["b"]} == {hub, citers[0]}

    def test_a_reciprocally_cited_pair_is_suppressed_too(self, rel_conn):
        """One EDGE can carry several mentions, and every other fixture here
        carries exactly one.

        Two cards that cite EACH OTHER produce two mentions collapsed onto one
        edge key by `edges[tuple(sorted(...))]`, and that is an ordinary shape
        — `tests/test_grouping_surface.py` already has a test built on it. The
        gap this closes was found by cross-model review: narrowing the branch to
        `... and len(edges[(a, b)]) == 1` left the whole class green while a
        mutually-cited declared-distinct pair was joined anyway.
        """
        a = add(rel_conn, "the first card, which points at the second one")
        b = add(rel_conn, f"the second card, which points back at {a}")
        findings.update_finding(rel_conn, a, append_note=f"see also {b}")
        relations.relate(rel_conn, a, "distinct_from", b, source="test")
        rep = grouping.citation_report(rel_conn)
        (sup,) = rep["suppressed_edges"]
        assert len(sup["mentions"]) == 2, (
            "fixture invalid: the pair must cite each other, or this edge "
            "carries one mention like every other fixture in the class"
        )
        assert rep["components_total"] == 0
        assert rep["still_grouped_total"] == 0

    def test_suppressed_edges_come_back_in_a_stable_order(self, rel_conn):
        """Every other list in this report is ordered deliberately; with one
        suppressed pair in the fixture the sort line is unobservable, so it
        takes two."""
        a = add(rel_conn, "the first card of the first declared-distinct pair")
        b = add(rel_conn, f"the second card of that pair, mentioning {a}")
        c = add(rel_conn, "the first card of the second declared-distinct pair")
        d = add(rel_conn, f"the second card of that other pair, mentioning {c}")
        relations.relate(rel_conn, a, "distinct_from", b, source="test")
        relations.relate(rel_conn, c, "distinct_from", d, source="test")
        rep = grouping.citation_report(rel_conn)
        assert rep["suppressed_total"] == 2
        pairs = [(e["a"], e["b"]) for e in rep["suppressed_edges"]]
        assert pairs == sorted(pairs)
        assert pairs == sorted([tuple(sorted((a, b))), tuple(sorted((c, d)))])

    def test_filing_lineage_is_not_suppressed(self, rel_conn):
        """The NON-SPREAD oracle, and it is as mandatory as the others.

        `split_from` is DECLARED, so suppressing it with `distinct_from` would
        be two declarations contradicting each other rather than a declaration
        beating a guess. Which of the two graphs owns that truth is CB-163,
        deliberately not answered here as a side effect.
        """
        add(rel_conn, "the parent card of a deliberate split", finding_id="CB-10")
        add(rel_conn, "the follow-up carved out of the parent card",
            finding_id="CB-9", meta={"split_from": "CB-10"})
        before = grouping.filing_report(rel_conn)
        relations.relate(rel_conn, "CB-9", "distinct_from", "CB-10", source="test")
        after = grouping.filing_report(rel_conn)
        assert before["lineages_total"] == 1
        assert after["lineages_total"] == 1
        assert {m["id"] for m in after["lineages"][0]["members"]} == {"CB-9", "CB-10"}

    def test_a_tracker_with_no_relations_table_reports_no_suppressions(self, conn):
        """The §4(3) DECISION, pinned: an ABSENT ledger means "nobody declared
        anything", never "I could not look".

        A suppression is an AFFIRMATIVE declaration, so where no ledger exists
        there are none and the empty set is the true answer rather than a
        degraded one — which is why this is an explicit probe and not a
        swallowed `OperationalError`, since that same exception is also what a
        disk error looks like. The plain `conn` fixture IS this tracker
        (findings schema, no relations schema), which is the measurement that
        ruled out simply trusting `db.connect()`'s schema registry: a caller
        holding its own connection never went through it.
        """
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='finding_relations'"
        ).fetchone()[0] == 0
        a = add(conn, "the card that the other one refers to")
        add(conn, f"another card that mentions {a} in passing")
        rep = grouping.citation_report(conn)
        assert rep["components_total"] == 1
        assert rep["suppressed_total"] == 0
        assert relations.active_suppressions(conn) == set()


class TestTagPivots:
    def test_counts_pairs_and_untagged(self, conn):
        add(conn, "first tagged card in the tag pivot test", tags=["alpha", "beta"])
        add(conn, "second tagged card in the tag pivot test", tags=["alpha", "beta"])
        add(conn, "third tagged card in the tag pivot test", tags=["alpha"])
        add(conn, "an untagged card in the tag pivot test")
        rep = grouping.tag_report(conn)
        assert rep["rows_untagged"] == 1
        assert rep["tags"][0] == {"tag": "alpha", "count": 3}
        assert rep["tag_applications"] == 5
        (pair,) = rep["pairs"]
        assert (pair["a"], pair["b"], pair["count"]) == ("alpha", "beta", 2)
        assert pair["jaccard"] == round(2 / 3, 3)

    def test_min_pair_count_filters(self, conn):
        add(conn, "a card carrying two tags that co-occur exactly once",
            tags=["one", "two"])
        assert grouping.tag_report(conn)["pairs"] == []
        assert grouping.tag_report(conn, min_pair_count=1)["pairs_total"] == 1

    def test_variants_span_tags_and_categories(self, conn):
        # Twin category spellings can no longer arise through the observation
        # path (CB-60 normalizes them at write time), but pre-CB-60 rows and
        # explicit-id adds still carry them verbatim — file the fixture through
        # the explicit-id path, which is exactly the legacy data the report-time
        # variant detection exists for.
        add(conn, "a card filed under the underscore spelling",
            category="process_improvement", tags=["follow-up"])
        findings.add_finding(
            conn, severity="medium", category="process-improvement", file="a.py",
            description="a card filed under the hyphen spelling",
            tags=["follow_up"], finding_id="CB-777",
        )
        rep = grouping.tag_report(conn)
        by_key = {v["key"]: v for v in rep["variants"]}
        assert {entry["label"] for entry in by_key["processimprovement"]["labels"]} == {
            "process_improvement", "process-improvement"}
        assert {entry["kind"] for entry in by_key["processimprovement"]["labels"]} == {
            "category"}
        assert {entry["label"] for entry in by_key["followup"]["labels"]} == {
            "follow-up", "follow_up"}

    def test_same_string_in_both_columns_is_not_a_variant(self, conn):
        add(conn, "a card whose tag and category are spelled identically",
            category="security", tags=["security"])
        assert grouping.tag_report(conn)["variants"] == []

    def test_malformed_tags_degrade_to_untagged(self, conn):
        a = add(conn, "a card whose tags blob is about to be corrupted")
        conn.execute("UPDATE findings SET tags = ? WHERE id = ?", ("{oops", a))
        conn.commit()
        rep = grouping.tag_report(conn)
        assert rep["rows_untagged"] == 1 and rep["tags_total"] == 0


class TestFilingLineage:
    def test_split_chain_is_one_lineage_with_depths(self, conn):
        a = add(conn, "the original card that was split into two follow-ups")
        b = add(conn, "the first split-off card", meta={"split_from": a})
        c = add(conn, "the card split off from the split-off card",
                meta={"split_from": b})
        rep = grouping.filing_report(conn)
        assert rep["lineages_total"] == 1, "A -> B -> C is ONE lineage, not three rows"
        lin = rep["lineages"][0]
        assert lin["roots"] == [a]
        assert {m["id"]: m["depth"] for m in lin["members"]} == {a: 0, b: 1, c: 2}

    def test_split_children_and_split_from_agree_on_one_edge(self, conn):
        a = add(conn, "the parent card naming its children explicitly")
        b = add(conn, "the child card naming its parent explicitly",
                meta={"split_from": a})
        findings.update_finding(conn, a, meta_update={
            "split_children": [f"{b} the child with a prose suffix"]})
        lin = grouping.filing_report(conn)["lineages"][0]
        assert lin["size"] == 2
        assert len(lin["links"]) == 1, "the same edge declared twice is one edge"
        assert {e["key"] for e in lin["links"][0]["evidence"]} == {
            "split_from", "split_children"}

    def test_lineage_survives_a_terminal_middle_card(self, conn):
        a = add(conn, "the origin card of a lineage whose middle gets fixed")
        b = add(conn, "the middle card of that lineage", meta={"split_from": a})
        c = add(conn, "the leaf card of that lineage", meta={"split_from": b})
        findings.update_finding(conn, b, status="fixed")
        lin = grouping.filing_report(conn)["lineages"][0]
        assert lin["size"] == 3, "links resolve over the whole tracker, not the filter"
        assert lin["in_population"] == 2
        assert {m["id"]: m["in_population"] for m in lin["members"]} == {
            a: True, b: False, c: True}

    def test_lineage_with_no_member_in_the_population_is_hidden(self, conn):
        a = add(conn, "a lineage parent that is entirely finished work")
        b = add(conn, "a lineage child that is entirely finished work",
                meta={"split_from": a})
        for i in (a, b):
            findings.update_finding(conn, i, status="fixed")
        assert grouping.filing_report(conn)["lineages_total"] == 0
        assert grouping.filing_report(conn, status="all")["lineages_total"] == 1

    def test_parent_key_builds_lineage_too(self, conn):
        a = add(conn, "an umbrella card with children filed under `parent`")
        kids = [add(conn, f"child card {i} of the umbrella", meta={"parent": a})
                for i in range(2)]
        lin = grouping.filing_report(conn)["lineages"][0]
        assert lin["roots"] == [a]
        assert {m["id"] for m in lin["members"]} == {a, *kids}

    def test_prose_value_without_an_id_is_reported_unresolved(self, conn):
        add(conn, "a card split from something that is not a codebugs card",
            meta={"split_from": "autosorter prod bug d7ec2391"})
        rep = grouping.filing_report(conn)
        assert rep["lineages_total"] == 0
        assert rep["unresolved_total"] == 1
        assert rep["unresolved_refs"][0]["key"] == "split_from"

    def test_meta_cycle_terminates_and_is_flagged(self, conn):
        a = add(conn, "one half of a deliberately cyclic parent declaration")
        b = add(conn, "other half of a deliberately cyclic parent declaration",
                meta={"parent": a})
        findings.update_finding(conn, a, meta_update={"parent": b})
        lin = grouping.filing_report(conn)["lineages"][0]
        assert lin["cyclic"] is True
        assert lin["size"] == 2

    def test_dangling_parent_id_is_not_an_edge(self, conn):
        add(conn, "a card whose declared parent does not exist in this tracker",
            meta={"split_from": "CB-9999"})
        rep = grouping.filing_report(conn)
        assert rep["lineages_total"] == 0 and rep["unresolved_total"] == 0

    def test_filing_events_group_by_exact_value(self, conn):
        for i in range(3):
            add(conn, f"card {i} filed during the same sprint",
                meta={"sprint": "sprint-oauth"})
        add(conn, "a card filed during a different sprint",
            meta={"sprint": "sprint-other"})
        add(conn, "two cards sharing a plan document, first",
            meta={"plan": "plans/x.md"})
        add(conn, "two cards sharing a plan document, second",
            meta={"plan": "plans/x.md"})
        rep = grouping.filing_report(conn)
        assert rep["events_total"] == 2, "a one-card event is not a group"
        assert [(e["key"], e["value"], e["size"]) for e in rep["events"]] == [
            ("sprint", "sprint-oauth", 3), ("plan", "plans/x.md", 2)]

    def test_rejects_negative_tuning(self, conn):
        with pytest.raises(ValueError, match="lineage_limit"):
            grouping.filing_report(conn, lineage_limit=-1)
