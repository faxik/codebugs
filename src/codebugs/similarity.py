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
or ids. meta.similar_to is an at-file-time advisory snapshot; readers should
resolve referenced ids' CURRENT status, and a re-scrub may rewrite the
annotation via update_finding (the reservation is add-side only).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Any

from codebugs import db, fmt
from codebugs.findings import LIVE_STATUSES, normalized_identity_text, similarity_candidates
from codebugs.types import is_text_filter_active, resolve_finding_status

# Calibrated on the 3162-row autosorter corpus (2026-08-16, reproducible via
# tests/manual/verify_similarity_corpus.py): 0.7 collapses 102 rows into 11
# families and splits the 115-row gate category into its ~10 genuinely distinct
# failure tails. The card's 0.95 was measured and rejected (77 rows, the target
# family does not unify); the change was notified per the letter-fix protocol.
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
# already stripped of it ("[0m[32m..." observed in the corpus).
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
        # zip() would silently truncate the dot product while the norms use all
        # components — a plausible-looking wrong number instead of an error.
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


# Memo keyed on CONTENT (description, meta_json), never on (id, created_at):
# ids recur across databases and created_at is whole-second, so an identity
# key silently serves one row's trigrams for another's text (caught by the
# test suite's in-memory DBs colliding within one second — the same collision
# any re-created tracker would hit). Content keys are self-versioning; the
# FIFO cap bounds memory (~2000 x ~2KB).
_norm_memo: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}


def _row_norm_tri(row: dict[str, Any]) -> tuple[str, frozenset[str]]:
    """(normalized_text, trigrams) for a candidate row, memoized with a FIFO cap.

    The memo is what keeps batch adds from re-scanning the pool N times
    (measured: a 92-row category costs ~24 ms per scan; a 100-member batch
    without the memo spends ~2.4 s inside one write-locked transaction).
    """
    key = (row["description"], row["meta_json"] or "")
    hit = _norm_memo.get(key)
    if hit is not None:
        return hit
    norm = normalize_text(row["description"], _parse_meta(row["meta_json"]))
    value = (norm, trigram_set(norm))
    if len(_norm_memo) >= _MEMO_CAP:
        _norm_memo.pop(next(iter(_norm_memo)))
    _norm_memo[key] = value
    return value


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
        conn,
        category=category,
        statuses=_ANNOTATE_STATUSES,
        limit=CANDIDATE_POOL_LIMIT,
        order="newest",
    )
    scored = []
    for row in rows:
        norm, tri = _row_norm_tri(row)
        if len(norm) < MIN_TEXT_LEN:
            continue
        score = jaccard(query_tri, tri)
        if score >= threshold:
            scored.append({"id": row["id"], "score": round(score, 3), "status": row["status"]})
    scored.sort(key=lambda m: (-m["score"], m["id"]))
    return scored[:limit]


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
    decision-stays-decided); ``status=`` widens explicitly (the sentinel
    ``"all"`` means every status) and the response names its populations.
    Short rows are excluded by the scoring-layer MIN_TEXT_LEN policy and
    counted. A pair scores by cosine when BOTH members have vectors, lexically
    otherwise.
    """
    _validate_score_params(threshold, family_limit)
    if member_limit is not None and member_limit < 0:
        raise ValueError(f"member_limit must be >= 0, got {member_limit}")
    if is_text_filter_active(category) and not category.strip():
        raise ValueError("category filter must not be blank")
    if status == "all":
        rows = similarity_candidates(conn, category=category)
        populations = ["all"]
    elif status is not None and status != "":
        resolved = resolve_finding_status(status)
        rows = similarity_candidates(conn, category=category, statuses=(resolved,))
        populations = [resolved]
    else:
        rows = similarity_candidates(conn, category=category, statuses=LIVE_STATUSES)
        populations = list(LIVE_STATUSES)

    recs, skipped = [], 0
    for row in rows:
        norm, tri = _row_norm_tri(row)
        if len(norm) < MIN_TEXT_LEN:
            skipped += 1
            continue
        recs.append({"row": row, "tri": tri, "vec": (vectors or {}).get(row["id"])})

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
                    edges.append(
                        {"a": a["row"]["id"], "b": b["row"]["id"], "score": round(score, 3)}
                    )

    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recs:
        members[dsu.find(rec["row"]["id"])].append(rec["row"])
    families = []
    for fam in members.values():
        if len(fam) <= 1:
            continue
        fam_id_set = {m["id"] for m in fam}
        fam_edges = [e for e in edges if e["a"] in fam_id_set]
        fam_sorted = sorted(fam, key=lambda m: (m["created_at"], m["id"]))
        families.append(
            {
                "category": fam_sorted[0]["category"],
                "size": len(fam_sorted),
                "min_pair_score": min(e["score"] for e in fam_edges),
                "edge_count": len(fam_edges),
                "edges": fam_edges,
                "members": [
                    {
                        "id": m["id"],
                        "status": m["status"],
                        "severity": m["severity"],
                        "occurrence_count": m["occurrence_count"],
                        "created_at": m["created_at"],
                        "file": m["file"],
                        "description_excerpt": m["description"][:_EXCERPT_LEN],
                    }
                    for m in fam_sorted
                ],
            }
        )
    families.sort(key=lambda f: (-f["size"], f["members"][0]["id"]))
    families_total = len(families)
    members_total = sum(f["size"] for f in families)
    # collapse_count is a statistic over ALL families — truncation changes the
    # page, never the statistic.
    collapse_count = members_total - families_total
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
        "collapse_count": collapse_count,
        "families": families,
        "families_total": families_total,
        "members_total": members_total,
    }
