"""Shared formatting utilities for CLI output."""

from __future__ import annotations


def empty_page_line(limit: object, corpus_count: object, *, empty: str, requested: str) -> str:
    """The line a CLI verb prints when its page came back empty (CB-210).

    An empty page has TWO causes and only one of them is a fact about the
    tracker. `(no findings match)` is a statement about the CORPUS, and over a
    full tracker asked for zero rows it is simply false — the MCP surface of the
    same verb has always been honest, because it returns `total` rather than a
    canned sentence. The number that tells the truth was already computed and
    sitting in the same result dict; the CLI threw it away on a bare `return`.

    THE PREDICATE LIVES HERE, ONCE, AND THAT IS THE POINT. Three verbs need it
    (`query`, `reqs-query`, `sweep-next`) across three domain modules that this
    project's rules forbid from reaching into each other, so the choice was one
    shared function or three hand-written copies. A predicate duplicated rather
    than shared is one edit away from disagreeing with itself — the same reason
    `is_sql_identifier` and `require_row_limit` are each the only copy of their
    pattern — and the copies would drift in the direction that matters, since
    the subtle half of this condition is the second clause rather than the first.

    BOTH CLAUSES ARE LOAD-BEARING. `limit == 0` alone is not enough: with
    `--ids` naming rows that do not exist, `query_findings` raises the limit to
    fit the id list, so the page is empty because the CORPUS had nothing, and
    saying "you asked for zero rows" there would trade one false statement for
    another. Requiring `corpus_count > 0` means the sentence is only ever
    printed on affirmative proof that something was there to return — the same
    shape as `reconcile.live_source_clause` and `search_similar`'s refusal,
    where an exclusion or a claim is made only when the evidence is positive.

    A GENUINELY EMPTY RESULT KEEPS ITS TEXT, TO THE BYTE. That is not politeness
    about a hot verb's output; it is what keeps this change narrow enough to be
    a truth fix rather than a redesign of every empty page in the CLI.

    `limit` is compared with `==` against an int and never for truthiness,
    because `None` (flag absent) and `0` (flag given as zero) mean different
    things here and truthiness cannot tell them apart — the CB-25/CB-82 shape.
    A non-int `limit` simply fails the comparison and takes the honest `empty`
    branch, so a caller cannot get the claim by passing something exotic.

    Args:
        limit: the limit the CALLER asked for -- `None` when the flag was absent.
        corpus_count: how many rows actually matched, ignoring the limit.
        empty: the byte-identical text for a genuinely empty result.
        requested: template for the honest line, taking one `{n}` placeholder.
    """
    if limit == 0 and isinstance(corpus_count, int) and corpus_count > 0:
        return requested.format(n=corpus_count)
    return empty


def format_table(rows: list[dict], columns: list[str], max_widths: dict | None = None) -> str:
    if not rows:
        return "(no results)"
    max_widths = max_widths or {}
    col_widths = {}
    for col in columns:
        header_w = len(col)
        data_w = max((len(str(row.get(col, ""))) for row in rows), default=0)
        w = max(header_w, data_w)
        if col in max_widths:
            w = min(w, max_widths[col])
        col_widths[col] = w

    fmt = "  ".join(f"{{:<{col_widths[c]}}}" for c in columns)
    lines = [fmt.format(*columns)]
    lines.append(fmt.format(*("-" * col_widths[c] for c in columns)))
    for row in rows:
        vals = []
        for c in columns:
            v = str(row.get(c, ""))
            w = col_widths[c]
            if len(v) > w:
                v = v[: w - 1] + "…"
            vals.append(v)
        lines.append(fmt.format(*vals))
    return "\n".join(lines)
