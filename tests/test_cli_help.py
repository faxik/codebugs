"""`codebugs --help` names a first step, and every verb's own `--help` still builds (T-75).

Two concerns, kept apart because they have different oracles:

- The top-level parser's `epilog=` carries a short "getting started" block, checked
  by substring presence (never a verbatim paragraph — a wording edit must not turn
  this red).
- `build_parser` wires ONE `formatter_class`-carrying `ArgumentParser` per verb, all
  descending from the top-level parser's own construction. This unit's brief warns
  that changing the top-level parser's `formatter_class` is exactly the place a
  one-parser edit silently breaks eighty other verbs' `--help` — and the top-level
  golden gate (`tests/test_cli_surface.py`) cannot see it, because it snapshots
  argparse Actions, never rendered help TEXT. So this file renders every verb's
  help directly, through the same `cli.build_parser` the real CLI and the golden
  both use (never a hand-built subparser tree — see `tests/cli_surface.py`'s own
  docstring for why that matters).
"""

from __future__ import annotations

from codebugs import cli


def test_help_epilog_names_a_first_step():
    parser, _sub, _commands = cli.build_parser()
    epilog = parser.epilog
    assert isinstance(epilog, str)
    assert epilog.strip()
    # Presence of the three "start here" beats named in the brief (§2): create a
    # tracker, file the first finding, look at the queue. Not a verbatim string —
    # a wording edit must not turn this red.
    for verb in ("init", "add", "query"):
        assert f"codebugs {verb}" in epilog, f"epilog does not mention `codebugs {verb}`"


class TestEveryVerbHelpStillBuilds:
    """Composition, not just the top-level parser.

    `test_help_epilog_names_a_first_step` above only exercises the TOP-LEVEL
    parser's own `--help`; this class renders every VERB's `--help` too,
    through the same `cli.build_parser` the real CLI and the golden both use.

    This class does NOT catch a `formatter_class` set on the top-level
    parser breaking a subparser's help. It was written expecting to (T-75's
    brief asked for exactly that), but T-75's own acceptor MEASURED the
    opposite: argparse's `formatter_class` is a per-parser argument each
    subparser receives from its own `add_parser(...)` call, never inherited
    from the parent parser's construction — so a top-level-only edit to it
    cannot reach a subparser at all, and no test of subparser help can
    observe that edit. What this class actually catches is breakage
    introduced in the VERBS themselves — a subparser definition that raises
    or renders empty text when `format_help()` is called on it.
    """

    def test_top_level_help_still_builds(self):
        parser, _sub, _commands = cli.build_parser()
        text = parser.format_help()
        assert text.strip()

    def test_every_verb_help_still_builds(self):
        parser, sub, _commands = cli.build_parser()
        assert sub.choices, "premise: build_parser wires at least one verb"
        failures = []
        for name, subparser in sub.choices.items():
            try:
                text = subparser.format_help()
            except Exception as exc:  # noqa: BLE001 - collect every failure, not just the first
                failures.append(f"{name}: {exc!r}")
                continue
            if not text.strip():
                failures.append(f"{name}: format_help() returned empty text")
        assert not failures, "verb(s) whose --help broke:\n" + "\n".join(failures)
