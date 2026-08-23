"""Regression gate: the CLI surface (CB-146) must not drift unintentionally.

See `tests/cli_surface.py` for what is captured and why (generic snapshot over
`vars(action)`, built through `cli.build_parser` — the real registration path
— with the CLI-provider registry supplying the domain list, never a hardcoded
one). This file is deliberately thin: the collection logic lives in that
module so the golden generator and this gate cannot disagree about what the
surface is, mirroring `tests/_mcp_schema.py` / `tests/test_boundary.py` for
the MCP wire golden.
"""

from __future__ import annotations

import json
import pathlib

from tests.cli_surface import collect_cli_surface


class TestCliSurfaceGolden:
    GOLDEN = pathlib.Path(__file__).parent / "golden" / "cli_surface.json"

    def test_surface_matches_golden(self):
        """Every verb's every argparse-action attribute must match the golden.

        If this fails: either (a) you intentionally changed a verb's CLI surface
        — regenerate the golden with
        `PYTHONPATH=src uv run python tests/cli_surface.py > tests/golden/cli_surface.json`,
        or (b) you accidentally drifted — fix the offending change.
        """
        assert self.GOLDEN.exists(), (
            f"Golden file missing at {self.GOLDEN}. Regenerate with the dump command above."
        )
        expected = json.loads(self.GOLDEN.read_text())
        current = collect_cli_surface()

        if current != expected:
            cur_verbs = set(current)
            exp_verbs = set(expected)
            added = sorted(cur_verbs - exp_verbs)
            removed = sorted(exp_verbs - cur_verbs)
            drifted = sorted(v for v in (cur_verbs & exp_verbs) if current[v] != expected[v])
            detail = ""
            if drifted:
                # Name the first attribute that actually differs, per drifted
                # verb, rather than dumping the whole nested structure — with
                # 67 verbs and up to a dozen actions each, a bare `!=` gives a
                # reader nothing to act on.
                lines = []
                for v in drifted:
                    cur_actions = current[v]["actions"]
                    exp_actions = expected[v]["actions"]
                    if len(cur_actions) != len(exp_actions):
                        lines.append(
                            f"  {v}: {len(exp_actions)} actions -> {len(cur_actions)}"
                        )
                        continue
                    for i, (ca, ea) in enumerate(zip(cur_actions, exp_actions)):
                        if ca != ea:
                            keys = sorted(set(ca) | set(ea))
                            for k in keys:
                                if ca.get(k) != ea.get(k):
                                    lines.append(
                                        f"  {v}[{i}].{k}: {ea.get(k)!r} -> {ca.get(k)!r}"
                                    )
                detail = "\ndrifted:\n" + "\n".join(lines)
            assert current == expected, (
                f"CLI surface drifted from golden.\nadded verbs: {added}\n"
                f"removed verbs: {removed}{detail}"
            )
