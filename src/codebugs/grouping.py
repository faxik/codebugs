"""Grouping extension: the axes the tracker STORES but never let anyone query.

Similarity is one grouping axis and codebugs already has it (similarity.py). On
a real 3206-row corpus it is also the weakest one: exactly ONE similarity family
(two cards) exists across the 1093 open cards, measured three times. The axes
that actually carve that backlog into work units were the ones with no query
surface at all:

  * CITATIONS — the CB-ids a card's own text names. ~2200 hand-written
    cross-references on the reference corpus, and the strongest evidence two
    cards are one piece of work, precisely because a human wrote each link.
  * TAGS — 2169 distinct labels over the open set, the strongest cross-cutting
    axis, together with the taxonomy drift that splinters it (`process_improvement`
    and `process-improvement` are two live categories in the same tracker).
  * FILING EVENTS — split_from / split_children / parent lineage, and the
    sprint / plan a batch of cards was filed under.

Contract, identical to similarity.py's: ZERO SQL in this module — every row
arrives through the public accessor of the module that OWNS its table
(module-ownership rule): finding rows through findings.grouping_candidates, and
the declared distinct_from suppressions through relations.active_suppressions —
and READ-ONLY; nothing here changes identity, status, ids, or meta. The second
accessor is named here rather than left to be discovered because "every row
arrives through grouping_candidates" was true when there was one source, and a
contract stated for one source does not survive a second one unremarked.

HUBS, and why a raw citation graph is useless. Connected components over the
whole reference graph do not decompose this corpus: on the open set the largest
component holds 327 of the 524 linked cards, because a handful of much-cited
cards (a known-trap card, a policy card) glue every neighbourhood together.
Such a card is not a member of one work unit; it is a landmark many work units
point AT. So a node whose degree exceeds ``hub_degree`` is not allowed to
transmit connectivity: it is reported as an ANCHOR with its citers listed (the
terminal-anchor shape), and the components either side of it stay separate. At
the default the same corpus yields 117 components, largest 11 — see the sweep
table at DEFAULT_HUB_DEGREE for why that value and not the histogram elbow.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

from codebugs import db
from codebugs.findings import (
    LIVE_STATUSES,
    grouping_candidates,
    parse_meta,
    parse_tags,
)
from codebugs.relations import active_suppressions
from codebugs.types import (
    FINDING_ID_PREFIX,
    is_text_filter_active,
    is_vocabulary_filter_active,
    resolve_finding_status,
)

# Degree above which a node stops transmitting connectivity (see module
# docstring). Callers override it; ``None`` disables hub splitting and returns
# the raw components.
#
# 3 is chosen on the OUTCOME, not on the shape of the degree histogram — the
# histogram elbow is at 4, and 4 is measurably the wrong answer. Swept over the
# 1096-card open population of the reference corpus:
#
#   hub_degree | hubs | components | LARGEST | cards grouped
#   -----------+------+------------+---------+--------------
#   none       |    0 |         64 |     345 |           546
#   6          |   18 |         78 |     229 |           494
#   4          |   53 |        104 |      55 |           417
#   3          |   97 |        117 |      11 |           335
#   2          |  179 |         87 |       5 |           204
#
# The point of the split is to hand someone a unit of work. A 55-card component
# is not one, so the extra 82 cards that 4 keeps in the graph are not a gain —
# they are the hairball surviving under a smaller name. 3 is the largest value
# whose worst component still fits the 3-12 card band the axis was adopted for,
# and 2 pays for its tidiness by dropping nearly half the grouped cards.
DEFAULT_HUB_DEGREE = 3

CITATION_RE = re.compile(rf"\b{re.escape(FINDING_ID_PREFIX)}(\d+)\b")

# Meta keys naming a card's PARENT / CHILDREN in a split. The values are prose
# in the wild ("CB-3143 folder_rules DELETE over paths"), so they are scanned
# for an id rather than compared as ids. `parent` is included on purpose: the
# brief named split_from/split_children, but `parent` is the same relation with
# 20x the rows on the reference corpus, and a lineage view that ignored it would
# be answering the letter of the request against the wrong data.
LINEAGE_PARENT_KEYS = ("split_from", "parent")
LINEAGE_CHILD_KEYS = ("split_children",)
# Meta keys naming the FILING EVENT a batch of cards was created under. Free
# text, grouped by exact value — normalizing them would merge two sprints whose
# names differ meaningfully.
FILING_EVENT_KEYS = ("sprint", "plan")

_CONTEXT_CHARS = 80
_EXCERPT_LEN = 200
_WS = re.compile(r"\s+")


class DSU:
    """Union-find. The package's ONE copy — similarity.py's families and this
    module's citation components and split lineages are the same primitive over
    three different edge sets, and two copies would be one edit away from
    disagreeing about what a component is."""

    def __init__(self, ids: list[str]) -> None:
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


def _population(
    conn: sqlite3.Connection, status: str | None, category: str | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve the caller's status filter into rows + the names of what it read.

    group_report's three-arm shape, kept identical so a reader comparing the two
    grouping surfaces sees one policy: the sentinel ``"all"`` means every status,
    an active vocabulary filter means exactly that status, and the default is the
    LIVE set. The sentinel test is type-pinned and the filter test is the shared
    predicate for CB-25's reason — ``unittest.mock.ANY`` compares equal to both a
    bare ``== "all"`` and a bare ``!= ""``, silently widening the population.
    """
    if is_text_filter_active(category) and not category.strip():
        raise ValueError("category filter must not be blank")
    if isinstance(status, str) and str.__eq__(status, "all"):
        return grouping_candidates(conn, category=category), ["all"]
    if is_vocabulary_filter_active(status):
        resolved = resolve_finding_status(status)
        return (
            grouping_candidates(conn, category=category, statuses=(resolved,)),
            [resolved],
        )
    return (
        grouping_candidates(conn, category=category, statuses=LIVE_STATUSES),
        list(LIVE_STATUSES),
    )


def _member(row: dict[str, Any]) -> dict[str, Any]:
    """The card summary every view in this module renders."""
    return {
        "id": row["id"],
        "status": row["status"],
        "severity": row["severity"],
        "category": row["category"],
        "file": row["file"],
        "created_at": row["created_at"],
        "description_excerpt": (row["description"] or "")[:_EXCERPT_LEN],
    }


# --- Citations ----------------------------------------------------------------


def extract_citations(text: str) -> list[str]:
    """Every distinct CB-id named in `text`, in order of first appearance."""
    seen: dict[str, None] = {}
    for n in CITATION_RE.findall(text or ""):
        seen.setdefault(f"{FINDING_ID_PREFIX}{n}", None)
    return list(seen)


def citation_context(text: str, target: str, width: int = _CONTEXT_CHARS) -> str:
    """The quoted window around the FIRST mention of `target` in `text`.

    An edge without the sentence that produced it is unusable — the operator
    cannot tell "duplicate of CB-9" from "unlike CB-9" — so every edge this
    module emits carries one of these.
    """
    match = re.search(rf"\b{re.escape(target)}\b", text or "")
    if match is None:
        return ""
    start, end = match.start(), match.end()
    left, right = max(0, start - width), min(len(text), end + width)
    snippet = _WS.sub(" ", text[left:right]).strip()
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def _row_texts(row: dict[str, Any]) -> list[tuple[str, str]]:
    """(field, text) for every citation-bearing string on a card.

    The description plus every string in `meta` — which is where the notes
    history lives, and also `related`, `blocks`, `recurrence_of` and friends.
    Restricting this to `meta.notes` was measured and costs 163 references on
    the reference corpus for no gain in precision; anything non-string is
    skipped, which is what keeps the identity machinery's `occurrences` ring out.
    """
    out = [("description", row["description"] or "")]
    meta = parse_meta(row["meta_json"])
    for key in sorted(meta):
        value = meta[key]
        if isinstance(value, str):
            out.append((f"meta.{key}", value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    out.append((f"meta.{key}", item))
    return out


def citation_report(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    category: str | None = None,
    hub_degree: int | None = DEFAULT_HUB_DEGREE,
    component_limit: int | None = None,
    member_limit: int | None = None,
    anchor_limit: int = 25,
    orphan_limit: int = 50,
) -> dict[str, Any]:
    """Connected components of the hand-written CB-id reference graph.

    READ-ONLY, and an ANNOTATION of what people already wrote — no link here is
    inferred. Every edge carries the quoted context of its first mention and the
    field it came from, because a component is only actionable if its edges are
    explained. Nodes above ``hub_degree`` become anchors instead of connectors
    (module docstring); ``hub_degree=None`` returns the raw components.

    References to ids outside the population (a fixed card, a foreign tracker)
    are COUNTED as dangling rather than dropped — the count is how the operator
    learns that a status filter cut the graph.

    A DECLARED ``distinct_from`` SUPPRESSES THE UNION, NOT THE EDGE (CB-62).
    Note the two halves of "no link here is inferred" above: the MENTION is
    hand-written, and the conclusion drawn from it — that the two cards are one
    unit of work — is this module's guess. So a human's explicit "these are
    different defects" (``relations_relate … distinct_from``) stops that edge
    joining the two cards, while the edge itself stays in the output with its
    quoted context, because the reference really was written and hiding it would
    destroy evidence rather than correct a conclusion.

    THE HONEST SCOPE: it suppresses one EDGE, not the PAIR. Removing an edge
    does not cut a graph — one third card citing both sides is enough to leave
    them in one component — and the measurement to weigh that against is the
    module docstring's own: 327 of 524 linked cards sat in a single component
    before hub splitting, which is not a graph where alternative paths are
    exotic. The report does not go quiet
    about it: every entry in ``suppressed_edges`` carries ``still_grouped``, and
    ``still_grouped_total`` counts them, so a declaration that lost is a fact
    the reader is handed rather than one they must notice. Cutting for real
    means deciding which side the third card belongs to, which is a different
    and much larger design.

    ``suppressed_edges`` deliberately takes no ``…_limit``, alone among this
    report's lists, and the reason is what bounds it: ``anchors`` and
    ``orphans`` grow with the CORPUS, while this one is bounded by the
    ``distinct_from`` rows a human typed one at a time. If that ever stops being
    true it needs a limit like its neighbours.

    The two graphs are NOT unified: ``filing_report``'s declared lineage is
    untouched, for the reason spelled out at the suppression site.
    """
    if hub_degree is not None and hub_degree < 0:
        raise ValueError(f"hub_degree must be >= 0 or None, got {hub_degree}")
    for name, limit in (("component_limit", component_limit),
                        ("member_limit", member_limit),
                        ("anchor_limit", anchor_limit),
                        ("orphan_limit", orphan_limit)):
        if limit is not None and limit < 0:
            raise ValueError(f"{name} must be >= 0, got {limit}")

    rows, populations = _population(conn, status, category)
    by_id = {r["id"]: r for r in rows}

    mentions: list[dict[str, str]] = []
    self_refs = dangling = 0
    for row in rows:
        # ONE unit for all three counters: the distinct (citing card, cited id)
        # pair. The dedup has to happen BEFORE the classification, not inside
        # the in-population arm — counting mentions per row but dangling and
        # self-references per occurrence made the three numbers on the same
        # header line incomparable, and inflated dangling 3x on the reference
        # corpus (a card repeating "CB-2136" in description, notes and
        # `related` is one relationship, not three).
        seen: set[str] = set()
        for field, text in _row_texts(row):
            for target in extract_citations(text):
                if target in seen:
                    continue
                seen.add(target)
                if target == row["id"]:
                    self_refs += 1
                    continue
                if target not in by_id:
                    dangling += 1
                    continue
                mentions.append({
                    "src": row["id"], "dst": target, "field": field,
                    "context": citation_context(text, target),
                })

    edges: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for m in mentions:
        edges[tuple(sorted((m["src"], m["dst"])))].append(m)
    degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    for m in mentions:
        in_degree[m["dst"]] += 1

    hubs = (
        {n for n, d in degree.items() if d > hub_degree}
        if hub_degree is not None
        else set()
    )
    # DECLARED beats INFERRED, and ONLY in that direction (CB-62).
    #
    # A citation edge is INFERRED: this module found the text `CB-N` inside a
    # card and guessed a relationship. A `distinct_from` edge is a human saying
    # "these are different defects, do not confuse them". A guess must lose to a
    # declaration, which is the whole reason `distinct_from` exists — and until
    # this line it suppressed nothing anywhere in the package.
    #
    # WHAT THIS DOES NOT DO, said here because the next reader will otherwise
    # read a partial suppression as an unfinished one. The two graphs are NOT
    # unified. `filing_report`'s lineage edges (`meta.split_from` /
    # `split_children`) are themselves DECLARED, so suppressing one with the
    # other would not be a declaration overriding a guess — it would be two
    # declarations contradicting each other, which is a question about WHICH
    # graph owns the truth. `split_from` currently has two independent
    # representations, a relation type in `finding_relations` and a meta key
    # this module reads, and reconciling them is CB-163: a decision that is
    # deliberately waiting for measured demand rather than being taken here as
    # a side effect. Read once per traversal, never per edge (CB-31's rule).
    suppressed = active_suppressions(conn)
    suppressed_edges: list[dict[str, Any]] = []
    dsu = DSU(list(by_id))
    for a, b in edges:
        # THE DECLARATION IS CONSULTED BEFORE THE HUB RULE, and that order is
        # load-bearing rather than incidental. Both branches `continue`, so the
        # GROUPING is identical either way; what changes is what the report can
        # SAY. With the hub test first, a declaration on an edge touching a
        # landmark card never reached this line at all, and `suppressed_total`
        # came back `0` with a live declaration sitting in the ledger — "no such
        # channel" wearing the face of "looked, nothing fired", which is the one
        # inversion the `attention` / `stripped_meta_keys` discipline forbids.
        # The damage also ran the wrong way round: a much-cited card is exactly
        # the one somebody bothers to declare distinct from its neighbours.
        if (a, b) in suppressed:
            # The EDGE survives — somebody wrote that reference, and hiding it
            # would destroy evidence rather than correct a conclusion — so only
            # the union is skipped, and the edge is reported here because with
            # the pair in no component there is nowhere else it could appear.
            # `relations.unrelate`'s own docstring names the hazard this closes:
            # "a suppression that should not exist is invisible by construction".
            suppressed_edges.append({"a": a, "b": b, "mentions": edges[(a, b)]})
            continue
        if a in hubs or b in hubs:
            continue
        dsu.union(a, b)
    # A DECLARATION CAN STILL BE DEFEATED, and staying silent about that would
    # undo the point of reporting suppressions at all. Skipping ONE union does
    # not cut a graph: with A–C and C–B unsuppressed, declaring A and B distinct
    # leaves them in one component through C, and the direct A–B edge is then
    # listed BOTH here and among that component's own edges — two lines of one
    # report asserting opposite things, which is the CB-48 shape. Making the cut
    # real would mean deciding which side C belongs to; that is a genuinely
    # different and much larger design, so this states the outcome rather than
    # pretending. Computed after the whole loop, because `dsu.find` means
    # nothing until every union has been applied.
    for sup in suppressed_edges:
        sup["still_grouped"] = dsu.find(sup["a"]) == dsu.find(sup["b"])
    suppressed_edges.sort(key=lambda e: (e["a"], e["b"]))
    still_grouped_total = sum(1 for e in suppressed_edges if e["still_grouped"])

    grouped: dict[str, list[str]] = defaultdict(list)
    for cid in by_id:
        grouped[dsu.find(cid)].append(cid)

    components = []
    for root, ids in grouped.items():
        if len(ids) <= 1:
            continue
        member_ids = sorted(ids, key=lambda i: (by_id[i]["created_at"], i))
        inside = set(member_ids)
        comp_edges = [
            {"a": a, "b": b, "mentions": ms}
            for (a, b), ms in edges.items()
            if a in inside and b in inside
        ]
        comp_edges.sort(key=lambda e: (e["a"], e["b"]))
        # Which landmarks this component hangs off. Without it a hub-split
        # component looks unmotivated: the reader cannot see that these six
        # cards are separated from those five only by CB-2136 sitting between.
        neighbours = sorted({
            other
            for (a, b) in edges
            for node, other in ((a, b), (b, a))
            if node in inside and other in hubs
        })
        components.append({
            "size": len(member_ids),
            "root_id": min(member_ids),
            "members": [_member(by_id[i]) for i in member_ids],
            "edges": comp_edges,
            "edge_count": len(comp_edges),
            "hub_neighbours": neighbours,
        })
    components.sort(key=lambda c: (-c["size"], c["root_id"]))
    components_total = len(components)
    cards_in_components = sum(c["size"] for c in components)

    anchors = []
    for cid, n in in_degree.items():
        if n < 2:
            continue
        citers = sorted(
            ({"id": m["src"], "field": m["field"], "context": m["context"]}
             for m in mentions if m["dst"] == cid),
            key=lambda c: c["id"],
        )
        anchors.append({
            "id": cid, "in_degree": n, "degree": degree[cid],
            "is_hub": cid in hubs, "citers": citers,
            "card": _member(by_id[cid]),
        })
    anchors.sort(key=lambda a: (-a["in_degree"], a["id"]))
    anchors_total = len(anchors)

    orphan_ids = sorted(i for i in by_id if degree.get(i, 0) == 0)

    if component_limit is not None:
        components = components[:component_limit]
    if member_limit is not None:
        for comp in components:
            comp["members"] = comp["members"][:member_limit]
            kept = {m["id"] for m in comp["members"]}
            # Edges follow the member page, or a dense component's O(n^2) edge
            # list keeps the response unbounded while claiming to be paginated.
            comp["edges"] = [
                e for e in comp["edges"] if e["a"] in kept and e["b"] in kept
            ]
    return {
        "populations": populations,
        "hub_degree": hub_degree,
        "rows_considered": len(rows),
        "citations_total": len(mentions),
        "edges_total": len(edges),
        "self_references": self_refs,
        "dangling_total": dangling,
        "suppressed_edges": suppressed_edges,
        "suppressed_total": len(suppressed_edges),
        "still_grouped_total": still_grouped_total,
        "components": components,
        "components_total": components_total,
        "cards_in_components": cards_in_components,
        "hubs": sorted(hubs),
        "anchors": anchors[:anchor_limit],
        "anchors_total": anchors_total,
        "orphans": orphan_ids[:orphan_limit],
        "orphans_total": len(orphan_ids),
    }


# --- Tags ---------------------------------------------------------------------


def normalize_label(label: str) -> str:
    """Fold a tag/category label to its punctuation- and case-free skeleton.

    This is the near-duplicate DETECTOR, not a rewrite: `process_improvement`
    and `process-improvement` are two live categories on the reference corpus
    and every count computed over either one is wrong by the size of the other.
    """
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def tag_report(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    category: str | None = None,
    min_pair_count: int = 2,
    tag_limit: int | None = None,
    pair_limit: int | None = 50,
) -> dict[str, Any]:
    """Tag pivots: counts, co-occurrence, and near-duplicate taxonomy strings.

    Co-occurrence carries Jaccard alongside the raw count, because on a corpus
    with one 390-card tag the raw count ranks that tag's pairs first no matter
    how weak the association is.

    `variants` spans tags AND categories in one namespace: the drift is not
    confined to one column, and the worst case on the reference corpus is a tag
    and a category that are the same concept spelled two ways.
    """
    for name, limit in (("min_pair_count", min_pair_count),
                        ("tag_limit", tag_limit), ("pair_limit", pair_limit)):
        if limit is not None and limit < 0:
            raise ValueError(f"{name} must be >= 0, got {limit}")

    rows, populations = _population(conn, status, category)
    tag_counts: dict[str, int] = defaultdict(int)
    cat_counts: dict[str, int] = defaultdict(int)
    row_tags: list[list[str]] = []
    untagged = 0
    for row in rows:
        cat_counts[row["category"]] += 1
        tags = sorted(set(parse_tags(row["tags_json"])))
        if not tags:
            untagged += 1
        row_tags.append(tags)
        for t in tags:
            tag_counts[t] += 1

    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for tags in row_tags:
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                pair_counts[(tags[i], tags[j])] += 1
    pairs = [
        {
            "a": a, "b": b, "count": n,
            "jaccard": round(n / (tag_counts[a] + tag_counts[b] - n), 3),
        }
        for (a, b), n in pair_counts.items()
        if n >= min_pair_count
    ]
    pairs.sort(key=lambda p: (-p["count"], -p["jaccard"], p["a"], p["b"]))
    pairs_total = len(pairs)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kind, counts in (("tag", tag_counts), ("category", cat_counts)):
        for label, n in counts.items():
            buckets[normalize_label(label)].append(
                {"label": label, "kind": kind, "count": n})
    variants = []
    for key, labels in buckets.items():
        if len({entry["label"] for entry in labels}) < 2:
            continue
        labels.sort(key=lambda entry: (-entry["count"], entry["kind"], entry["label"]))
        variants.append({
            "key": key, "labels": labels,
            "total": sum(entry["count"] for entry in labels),
        })
    variants.sort(key=lambda v: (-v["total"], v["key"]))

    tags = [{"tag": t, "count": n} for t, n in tag_counts.items()]
    tags.sort(key=lambda t: (-t["count"], t["tag"]))
    tags_total = len(tags)
    return {
        "populations": populations,
        "rows_considered": len(rows),
        "rows_untagged": untagged,
        "tags": tags if tag_limit is None else tags[:tag_limit],
        "tags_total": tags_total,
        "tag_applications": sum(tag_counts.values()),
        "pairs": pairs if pair_limit is None else pairs[:pair_limit],
        "pairs_total": pairs_total,
        "variants": variants,
        "variants_total": len(variants),
    }


# --- Filing events ------------------------------------------------------------


def _lineage_ref(value: str) -> str | None:
    """The card a lineage meta value points at, or None if it names no card.

    Values are prose in the wild — 'CB-3143 folder_rules DELETE over paths' and
    'autosorter prod bug d7ec2391' are both real. The first is an edge; the
    second is reported as unresolved rather than silently dropped, because a
    lineage with a missing parent looks exactly like a root.
    """
    found = extract_citations(value)
    return found[0] if found else None


def _lineage_values(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def filing_report(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    category: str | None = None,
    lineage_limit: int | None = None,
    event_limit: int | None = None,
) -> dict[str, Any]:
    """Split lineages and shared filing events.

    LINEAGE IS TRAVERSED, NOT GROUPED. A → B → C is ONE lineage rendered as a
    tree with depths, not three rows keyed on a meta value; and the links are
    resolved against EVERY card in the tracker, not just the population, because
    a chain whose middle card is `fixed` is still one chain. A lineage surfaces
    when at least one member is in the population, and each member says whether
    it is.

    Filing events (sprint / plan) are grouped by exact value and restricted to
    the population — they are a filing fact about those cards, not a graph.
    """
    for name, limit in (("lineage_limit", lineage_limit),
                        ("event_limit", event_limit)):
        if limit is not None and limit < 0:
            raise ValueError(f"{name} must be >= 0, got {limit}")

    rows, populations = _population(conn, status, category)
    pop_ids = {r["id"] for r in rows}
    universe = grouping_candidates(conn)
    by_id = {r["id"]: r for r in universe}

    links: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    unresolved: list[dict[str, str]] = []
    for row in universe:
        meta = parse_meta(row["meta_json"])
        for key in LINEAGE_PARENT_KEYS:
            for value in _lineage_values(meta, key):
                ref = _lineage_ref(value)
                if ref is None:
                    unresolved.append({"id": row["id"], "key": key, "value": value})
                elif ref != row["id"] and ref in by_id:
                    links[(ref, row["id"])].append({"id": row["id"], "key": key,
                                                    "value": value})
        for key in LINEAGE_CHILD_KEYS:
            for value in _lineage_values(meta, key):
                ref = _lineage_ref(value)
                if ref is None:
                    unresolved.append({"id": row["id"], "key": key, "value": value})
                elif ref != row["id"] and ref in by_id:
                    links[(row["id"], ref)].append({"id": row["id"], "key": key,
                                                    "value": value})

    dsu = DSU(list(by_id))
    for parent, child in links:
        dsu.union(parent, child)
    grouped: dict[str, list[str]] = defaultdict(list)
    for cid in by_id:
        grouped[dsu.find(cid)].append(cid)

    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for parent, child in links:
        children[parent].append(child)
        parents[child].append(parent)

    lineages = []
    for ids in grouped.values():
        if len(ids) <= 1:
            continue
        if not (set(ids) & pop_ids):
            continue
        roots = sorted(i for i in ids if not parents.get(i))
        depth = {r: 0 for r in roots}
        queue = list(roots)
        # BFS with a visited set, so a meta cycle (A.parent=B, B.parent=A)
        # truncates instead of hanging the request.
        while queue:
            node = queue.pop(0)
            for child in sorted(children.get(node, ())):
                if child in depth:
                    continue
                depth[child] = depth[node] + 1
                queue.append(child)
        # Cycle members reachable from no root still belong to the lineage.
        orphaned = sorted(i for i in ids if i not in depth)
        for i in orphaned:
            depth[i] = 0
        members = sorted(ids, key=lambda i: (depth[i], by_id[i]["created_at"], i))
        lineages.append({
            "size": len(ids),
            "root_id": roots[0] if roots else members[0],
            "roots": roots,
            "cyclic": bool(orphaned),
            "in_population": sum(1 for i in ids if i in pop_ids),
            "members": [
                {**_member(by_id[i]), "depth": depth[i],
                 "in_population": i in pop_ids,
                 "parents": sorted(parents.get(i, ()))}
                for i in members
            ],
            "links": [
                {"parent": p, "child": c, "evidence": ev}
                for (p, c), ev in sorted(links.items())
                if p in set(ids)
            ],
        })
    lineages.sort(key=lambda lin: (-lin["size"], lin["root_id"]))
    lineages_total = len(lineages)
    cards_in_lineages = sum(lin["size"] for lin in lineages)

    events = []
    for key in FILING_EVENT_KEYS:
        by_value: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            value = parse_meta(row["meta_json"]).get(key)
            if isinstance(value, str) and value.strip():
                by_value[value].append(row["id"])
        for value, ids in by_value.items():
            if len(ids) < 2:
                continue
            events.append({
                "key": key, "value": value, "size": len(ids),
                "members": [_member(by_id[i]) for i in sorted(ids)],
            })
    events.sort(key=lambda e: (-e["size"], e["key"], e["value"]))
    events_total = len(events)

    return {
        "populations": populations,
        "rows_considered": len(rows),
        "universe_rows": len(universe),
        "lineages": lineages if lineage_limit is None else lineages[:lineage_limit],
        "lineages_total": lineages_total,
        "cards_in_lineages": cards_in_lineages,
        "unresolved_refs": unresolved,
        "unresolved_total": len(unresolved),
        "events": events if event_limit is None else events[:event_limit],
        "events_total": events_total,
        "event_keys": list(FILING_EVENT_KEYS),
        "lineage_keys": list(LINEAGE_PARENT_KEYS + LINEAGE_CHILD_KEYS),
    }


# --- Surfaces -----------------------------------------------------------------


def register_tools(mcp, conn_factory) -> None:
    """Register grouping MCP tools — thin forwards to the three reports."""

    @mcp.tool()
    def grouping_citations(
        status: str | None = None,
        category: str | None = None,
        hub_degree: int | None = DEFAULT_HUB_DEGREE,
        component_limit: int | None = None,
        member_limit: int | None = None,
        anchor_limit: int = 25,
        orphan_limit: int = 50,
    ) -> dict[str, Any]:
        """Connected components of the hand-written CB-id reference graph.

        READ-ONLY, and an ANNOTATION of what people already wrote — no link here
        is inferred. Every edge carries the field it came from and the quoted
        context of its first mention. A node whose degree exceeds hub_degree is
        a landmark many work units point AT, not a member of one: it does not
        transmit connectivity and is reported as an ANCHOR with its citers, so
        the components either side of it stay separate. References to ids
        outside the population are COUNTED as dangling, never dropped.

        A pair you have declared DIFFERENT — relations_relate(a, "distinct_from",
        b) — stops being joined BY THAT REFERENCE, in either order; retracting
        the declaration brings the grouping back. The citation itself still
        appears, with its quoted context, in suppressed_edges: your declaration
        corrects the conclusion this tool draws from the reference, not the fact
        that somebody wrote it. Read still_grouped on each entry before trusting
        the separation — dropping one reference does not cut a graph, so if a
        third card cites both they are in one component regardless, and that
        flag (counted by still_grouped_total) is how the report tells you your
        declaration lost rather than leaving you to notice. This is the ONLY
        place distinct_from suppresses: grouping_filing's declared lineage
        (split_from / split_children) is untouched, because overriding one
        declaration with another is a different question from overriding a
        guess.

        Args:
            status: Narrow/widen the population (default: live statuses; "all")
            category: Restrict to one category
            hub_degree: Degree above which a node becomes an anchor (default 3,
                chosen on the outcome — see DEFAULT_HUB_DEGREE); None disables
                hub splitting and returns the raw components
            component_limit: Max components returned (totals stay visible)
            member_limit: Max members per component (edges follow the page)
            anchor_limit: Max anchors returned (default 25)
            orphan_limit: Max orphan ids returned (default 50)
        """
        with conn_factory() as conn:
            return citation_report(
                conn,
                status=status,
                category=category,
                hub_degree=hub_degree,
                component_limit=component_limit,
                member_limit=member_limit,
                anchor_limit=anchor_limit,
                orphan_limit=orphan_limit,
            )

    @mcp.tool()
    def grouping_tags(
        status: str | None = None,
        category: str | None = None,
        min_pair_count: int = 2,
        tag_limit: int | None = None,
        pair_limit: int | None = 50,
    ) -> dict[str, Any]:
        """Tag pivots: counts, co-occurrence, and near-duplicate taxonomy strings.

        READ-ONLY. Co-occurrence carries Jaccard beside the raw count, because
        on a corpus with one 390-card tag the raw count ranks that tag's pairs
        first no matter how weak the association is. `variants` spans tags AND
        categories in one namespace: the taxonomy drift is not confined to one
        column (`process_improvement` / `process-improvement`).

        Args:
            status: Narrow/widen the population (default: live statuses; "all")
            category: Restrict to one category
            min_pair_count: Drop tag pairs co-occurring fewer times (default 2)
            tag_limit: Max tags returned (totals stay visible)
            pair_limit: Max pairs returned (default 50; None for all)
        """
        with conn_factory() as conn:
            return tag_report(
                conn,
                status=status,
                category=category,
                min_pair_count=min_pair_count,
                tag_limit=tag_limit,
                pair_limit=pair_limit,
            )

    @mcp.tool()
    def grouping_filing(
        status: str | None = None,
        category: str | None = None,
        lineage_limit: int | None = None,
        event_limit: int | None = None,
    ) -> dict[str, Any]:
        """Split lineages and shared filing events (sprint / plan).

        READ-ONLY. LINEAGE IS TRAVERSED, NOT GROUPED: A → B → C is one lineage
        with depths, and its links resolve against EVERY card in the tracker,
        not just the population, so a `fixed` middle card does not sever the
        chain. A lineage surfaces when at least one member is in the population;
        a lineage value naming no card is reported unresolved, not dropped.
        Filing events are grouped by exact value within the population.

        Args:
            status: Narrow/widen the population (default: live statuses; "all")
            category: Restrict to one category
            lineage_limit: Max lineages returned (totals stay visible)
            event_limit: Max filing events returned (totals stay visible)
        """
        with conn_factory() as conn:
            return filing_report(
                conn,
                status=status,
                category=category,
                lineage_limit=lineage_limit,
                event_limit=event_limit,
            )


def _hub_degree_arg(value: str) -> int | None:
    """argparse type for --hub-degree: an int, or `none` to disable hub splitting.

    The domain's None is a real setting (raw components), and `type=int` has no
    spelling for it — a CLI that could not reach it would be a hole against MCP.
    A negative int is passed through so the DOMAIN refuses it (one message, one
    place), not argparse.
    """
    if value.strip().lower() == "none":
        return None
    return int(value)


def register_cli(sub, commands) -> None:
    """Register grouping CLI subcommands."""
    import json
    import sys

    from codebugs.fmt import format_table

    p_cit = sub.add_parser(
        "grouping-citations",
        help="Components of the hand-written CB-id reference graph (read-only; "
        "nodes above --hub-degree become anchors, not connectors)",
    )
    p_cit.add_argument("--status", default=None)
    p_cit.add_argument("--category", default=None)
    p_cit.add_argument(
        "--hub-degree",
        type=_hub_degree_arg,
        default=DEFAULT_HUB_DEGREE,
        dest="hub_degree",
        help=f"degree above which a node is an anchor (default {DEFAULT_HUB_DEGREE}); "
        "'none' disables hub splitting and returns the raw components",
    )
    p_cit.add_argument("--component-limit", type=int, default=None, dest="component_limit")
    p_cit.add_argument("--member-limit", type=int, default=None, dest="member_limit")
    p_cit.add_argument("--anchor-limit", type=int, default=25, dest="anchor_limit")
    p_cit.add_argument("--orphan-limit", type=int, default=50, dest="orphan_limit")
    p_cit.add_argument("--json", action="store_true", dest="as_json")

    p_tags = sub.add_parser(
        "grouping-tags",
        help="Tag counts, co-occurrence (Jaccard beside raw count) and near-duplicate "
        "labels across tags AND categories (read-only)",
    )
    p_tags.add_argument("--status", default=None)
    p_tags.add_argument("--category", default=None)
    p_tags.add_argument("--min-pair-count", type=int, default=2, dest="min_pair_count")
    p_tags.add_argument("--tag-limit", type=int, default=None, dest="tag_limit")
    p_tags.add_argument("--pair-limit", type=int, default=50, dest="pair_limit")
    p_tags.add_argument("--json", action="store_true", dest="as_json")

    p_fil = sub.add_parser(
        "grouping-filing",
        help="Split lineages (traversed against the whole tracker, not grouped) and "
        "sprint/plan filing events (read-only)",
    )
    p_fil.add_argument("--status", default=None)
    p_fil.add_argument("--category", default=None)
    p_fil.add_argument("--lineage-limit", type=int, default=None, dest="lineage_limit")
    p_fil.add_argument("--event-limit", type=int, default=None, dest="event_limit")
    p_fil.add_argument("--json", action="store_true", dest="as_json")

    # All three handlers: no JSONDecodeError-first arm (the _cmd_update pattern).
    # These read-only reports parse stored meta/tags through the deliberately
    # tolerant parse_meta/parse_tags, so no stored-data JSONDecodeError can
    # surface here and the arm would assert a hazard that does not exist.

    def _run(args, fn, **kw):
        conn = db.connect()
        try:
            return fn(conn, status=args.status, category=args.category, **kw)
        except (KeyError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_grouping_citations(args) -> None:
        report = _run(
            args,
            citation_report,
            hub_degree=args.hub_degree,
            component_limit=args.component_limit,
            member_limit=args.member_limit,
            anchor_limit=args.anchor_limit,
            orphan_limit=args.orphan_limit,
        )
        if args.as_json:
            print(json.dumps(report, indent=2))
            return
        print(
            f"populations={report['populations']} rows={report['rows_considered']} "
            f"hub_degree={report['hub_degree']} citations={report['citations_total']} "
            f"edges={report['edges_total']} dangling={report['dangling_total']} "
            f"suppressed={report['suppressed_total']} "
            f"still_grouped={report['still_grouped_total']} "
            f"components={report['components_total']} "
            f"cards_in_components={report['cards_in_components']} hubs={len(report['hubs'])}"
        )
        # Printed BEFORE the components, and unconditionally when non-empty: a
        # declared distinct_from is the one thing in this report that makes
        # cards NOT group, so a reader looking for a component that is missing
        # must meet the reason before the list it is missing from.
        for sup in report["suppressed_edges"]:
            note = (
                " -- STILL GROUPED: another citation path connects them"
                if sup["still_grouped"]
                else ""
            )
            print(
                f"suppressed {sup['a']} <-> {sup['b']} "
                f"(declared distinct_from; {len(sup['mentions'])} mention(s)){note}"
            )
        if not report["components"]:
            print("No citation components.")
        for comp in report["components"]:
            print(
                f"\n[{comp['root_id']}] size={comp['size']} edges={comp['edge_count']} "
                f"hub_neighbours={','.join(comp['hub_neighbours']) or '-'}"
            )
            rows = [
                {
                    "id": m["id"],
                    "status": m["status"],
                    "sev": m["severity"],
                    "category": m["category"],
                    "description": m["description_excerpt"],
                }
                for m in comp["members"]
            ]
            print(format_table(rows, ["id", "status", "sev", "category", "description"],
                               max_widths={"description": 60}))
        shown = len(report["components"])
        if report["components_total"] > shown:
            print(f"\n({report['components_total'] - shown} more components truncated; "
                  "--component-limit)")
        if report["anchors"]:
            print("\nAnchors (most-cited cards):")
            rows = [
                {
                    "id": a["id"],
                    "in_degree": a["in_degree"],
                    "degree": a["degree"],
                    "hub": "yes" if a["is_hub"] else "",
                    "description": a["card"]["description_excerpt"],
                }
                for a in report["anchors"]
            ]
            print(format_table(rows, ["id", "in_degree", "degree", "hub", "description"],
                               max_widths={"description": 60}))
            if report["anchors_total"] > len(report["anchors"]):
                print(f"({report['anchors_total'] - len(report['anchors'])} more anchors "
                      "truncated; --anchor-limit)")

    def _cmd_grouping_tags(args) -> None:
        report = _run(
            args,
            tag_report,
            min_pair_count=args.min_pair_count,
            tag_limit=args.tag_limit,
            pair_limit=args.pair_limit,
        )
        if args.as_json:
            print(json.dumps(report, indent=2))
            return
        print(
            f"populations={report['populations']} rows={report['rows_considered']} "
            f"untagged={report['rows_untagged']} tags={report['tags_total']} "
            f"applications={report['tag_applications']} pairs={report['pairs_total']} "
            f"variants={report['variants_total']}"
        )
        if not report["tags"]:
            print("No tags.")
            return
        print(format_table(report["tags"], ["tag", "count"]))
        shown = len(report["tags"])
        if report["tags_total"] > shown:
            print(f"({report['tags_total'] - shown} more tags truncated; --tag-limit)")
        if report["pairs"]:
            print("\nCo-occurring pairs:")
            print(format_table(report["pairs"], ["a", "b", "count", "jaccard"]))
            if report["pairs_total"] > len(report["pairs"]):
                print(f"({report['pairs_total'] - len(report['pairs'])} more pairs truncated; "
                      "--pair-limit)")
        if report["variants"]:
            print("\nNear-duplicate labels (tags and categories):")
            rows = [
                {
                    "key": v["key"],
                    "total": v["total"],
                    "labels": ", ".join(
                        f"{e['label']} ({e['kind']}:{e['count']})" for e in v["labels"]
                    ),
                }
                for v in report["variants"]
            ]
            print(format_table(rows, ["key", "total", "labels"], max_widths={"labels": 80}))

    def _cmd_grouping_filing(args) -> None:
        report = _run(
            args,
            filing_report,
            lineage_limit=args.lineage_limit,
            event_limit=args.event_limit,
        )
        if args.as_json:
            print(json.dumps(report, indent=2))
            return
        print(
            f"populations={report['populations']} rows={report['rows_considered']} "
            f"universe={report['universe_rows']} lineages={report['lineages_total']} "
            f"cards_in_lineages={report['cards_in_lineages']} "
            f"unresolved={report['unresolved_total']} events={report['events_total']}"
        )
        if not report["lineages"]:
            print("No lineages.")
        for lin in report["lineages"]:
            print(
                f"\n[{lin['root_id']}] size={lin['size']} in_population={lin['in_population']}"
                f"{' cyclic' if lin['cyclic'] else ''}"
            )
            rows = [
                {
                    "depth": m["depth"],
                    "id": m["id"],
                    "status": m["status"],
                    "in_pop": "yes" if m["in_population"] else "",
                    "parents": ",".join(m["parents"]),
                    "description": m["description_excerpt"],
                }
                for m in lin["members"]
            ]
            print(format_table(rows, ["depth", "id", "status", "in_pop", "parents", "description"],
                               max_widths={"description": 60}))
        shown = len(report["lineages"])
        if report["lineages_total"] > shown:
            print(f"\n({report['lineages_total'] - shown} more lineages truncated; "
                  "--lineage-limit)")
        if not report["events"]:
            print("\nNo filing events.")
        for ev in report["events"]:
            print(f"\n{ev['key']}={ev['value']!r} size={ev['size']}")
            rows = [
                {"id": m["id"], "status": m["status"], "description": m["description_excerpt"]}
                for m in ev["members"]
            ]
            print(format_table(rows, ["id", "status", "description"],
                               max_widths={"description": 60}))
        shown = len(report["events"])
        if report["events_total"] > shown:
            print(f"\n({report['events_total'] - shown} more events truncated; --event-limit)")
        if report["unresolved_refs"]:
            print(f"\n{report['unresolved_total']} lineage value(s) name no card "
                  "(see --json unresolved_refs)")

    commands["grouping-citations"] = _cmd_grouping_citations
    commands["grouping-tags"] = _cmd_grouping_tags
    commands["grouping-filing"] = _cmd_grouping_filing


db.register_tool_provider("grouping", register_tools)
db.register_cli_provider("grouping", register_cli)
