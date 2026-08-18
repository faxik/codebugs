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
arrives through the findings.grouping_candidates accessor (module-ownership
rule) — and READ-ONLY; nothing here changes identity, status, ids, or meta.

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

from codebugs.findings import (
    LIVE_STATUSES,
    grouping_candidates,
    parse_meta,
    parse_tags,
)
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
    dsu = DSU(list(by_id))
    for a, b in edges:
        if a in hubs or b in hubs:
            continue
        dsu.union(a, b)

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
