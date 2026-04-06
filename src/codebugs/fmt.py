"""Shared formatting utilities for CLI output."""

from __future__ import annotations


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
