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

import pytest

from tests.cli_surface import _EXCLUDED_ACTION_ATTRS, collect_cli_surface

# A sentinel distinct from every real value a verb record's key can hold
# (JSON scalars, lists, `None`, booleans) — used so the detail loop below can
# tell "this key is truly absent on one side" apart from "this key's value
# happens to be `None`" without a second special case.
_MISSING_KEY = object()


def _fmt(value: object) -> str:
    """`repr()`, except the sentinel above prints as a readable label."""
    if value is _MISSING_KEY:
        return "<key missing>"
    return repr(value)


def _drift_message(current: dict, expected: dict) -> str:
    """Build the `CLI surface drifted from golden.` diagnostic for two surfaces.

    Extracted to a top-level function so a synthetic-fixture test can drive
    the EXACT code path the real gate runs, rather than a hand-copied twin of
    it that could silently disagree (this repo's own recurring lesson: a
    duplicated implementation is one drift away from disagreeing with the
    original). Precondition: `current != expected` (the caller checks this
    before calling, since building the message is pointless otherwise).
    """
    cur_verbs = set(current)
    exp_verbs = set(expected)
    added = sorted(cur_verbs - exp_verbs)
    removed = sorted(exp_verbs - cur_verbs)
    drifted = sorted(v for v in (cur_verbs & exp_verbs) if current[v] != expected[v])
    detail = ""
    if drifted:
        # Name the first attribute that actually differs, per drifted verb,
        # rather than dumping the whole nested structure — with this many
        # verbs and up to a dozen actions each, a bare `!=` gives a reader
        # nothing to act on.
        #
        # Walk the UNION of the verb record's own keys (CB-169), never a
        # named subset. The record used to carry only `actions`, and this
        # loop compared exactly that key by name; when Т-64 added
        # `verb_action` (CB-152) the loop had no way to know a fourth key
        # existed, so a `verb_action`-only drift produced a "drifted:"
        # header with an empty body — the gate stayed red (the raw
        # `assert current == expected` below is untouched), but the
        # diagnostic printed above it read as "nothing added, nothing
        # removed, nothing drifted", which is a false claim under a failing
        # assertion. Keying the walk on `set(cur) | set(exp)` instead of a
        # literal `["actions"]` means the next key this record ever grows is
        # covered without a second edit here.
        lines = []
        for v in drifted:
            cur_verb = current[v]
            exp_verb = expected[v]
            for k in sorted(set(cur_verb) | set(exp_verb)):
                cur_val = cur_verb.get(k, _MISSING_KEY)
                exp_val = exp_verb.get(k, _MISSING_KEY)
                if cur_val == exp_val:
                    continue
                if k == "actions" and isinstance(cur_val, list) and isinstance(exp_val, list):
                    # Keep the finer-grained per-action, per-attribute diff
                    # for this one key — it is a list of dicts, and a bare
                    # repr of the whole list is exactly the "reader gets
                    # nothing to act on" case this loop exists to avoid.
                    if len(cur_val) != len(exp_val):
                        lines.append(f"  {v}.actions: {len(exp_val)} actions -> {len(cur_val)}")
                        continue
                    for i, (ca, ea) in enumerate(zip(cur_val, exp_val)):
                        if ca != ea:
                            for ak in sorted(set(ca) | set(ea)):
                                if ca.get(ak) != ea.get(ak):
                                    lines.append(
                                        f"  {v}.actions[{i}].{ak}: {ea.get(ak)!r} -> {ca.get(ak)!r}"
                                    )
                else:
                    lines.append(f"  {v}.{k}: {_fmt(exp_val)} -> {_fmt(cur_val)}")
        detail = "\ndrifted:\n" + "\n".join(lines)
    return f"CLI surface drifted from golden.\nadded verbs: {added}\nremoved verbs: {removed}{detail}"


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

        assert current == expected, _drift_message(current, expected)


class TestExcludedActionAttrs:
    """`_EXCLUDED_ACTION_ATTRS` carries both halves of the discipline (CB-179).

    Every row of that table takes one argparse attribute out from under the
    golden comparison, and the golden is one of the two gates on what a
    command-line user sees. It used to be a bare `frozenset` whose entire
    justification lived in a comment above it, so a second row could be added
    with no reason at all and a row that had stopped exempting anything would
    sit there forever. Neither test below implies the other: a row can name a
    live attribute and carry no reason, and a reasoned row can name an
    attribute argparse stopped producing two releases ago.
    """

    @staticmethod
    def _attributes_argparse_actually_produces() -> set[str]:
        """Every `vars(action)` key the REAL parser yields, BEFORE exclusion.

        Built from `cli.build_parser` and walked exactly as
        `cli_surface.collect_cli_surface` walks it — top-level parser, then
        `sub.choices` — so the world this table is judged against is the one
        the golden is generated from.

        `collect_cli_surface()` itself cannot be reused here, and the reason is
        the point rather than an omission: it returns actions ALREADY passed
        through `_serialize_action`, which is what applies
        `_EXCLUDED_ACTION_ATTRS`. Asking it what argparse produces would be
        asking the exclusion whether it excludes anything — circular, and it
        would answer "no stale rows" by construction.

        Named rather than left to be discovered: `sub._choices_actions` holds
        pseudo-actions that `collect_cli_surface` also serializes, and this
        walk does not visit them. Measured today they contribute no attribute
        the walk does not already see, and the direction of the gap is a FALSE
        REFUSAL — an attribute only a pseudo-action carries would read as
        stale — which is the loud failure rather than the silent one.
        """
        from codebugs import cli

        parser, sub, _commands = cli.build_parser()
        seen: set[str] = set()
        for action in parser._actions:
            seen.update(vars(action))
        for subparser in sub.choices.values():
            for action in subparser._actions:
                seen.update(vars(action))
        assert seen, "premise: the real parser yields at least one action attribute"
        return seen

    def test_every_excluded_attribute_carries_a_real_reason(self):
        empty = [
            name
            for name, reason in _EXCLUDED_ACTION_ATTRS.items()
            if not isinstance(reason, str) or len(reason.strip()) < 20
        ]
        assert empty == [], (
            f"_EXCLUDED_ACTION_ATTRS row(s) with no real reason: {empty} — an "
            "exclusion here removes an attribute from the snapshot that gates "
            "the user-visible CLI, so it owes an argument, not a name."
        )

    def test_no_excluded_attribute_is_stale(self):
        """Self-deleting: a row exempting nothing must be removed.

        An attribute argparse no longer sets is not being excluded from
        anything, and leaving the row is how a table becomes a standing licence
        nobody re-reads.
        """
        produced = self._attributes_argparse_actually_produces()
        stale = sorted(name for name in _EXCLUDED_ACTION_ATTRS if name not in produced)
        assert stale == [], (
            f"_EXCLUDED_ACTION_ATTRS names {stale}, which no `vars(action)` in "
            "the real parser returns any more — delete the row. This table may "
            "only SHRINK."
        )


class TestVerbActionsByIdentityAliasBoundary:
    """CB-168: a verb registered with `aliases=[...]` must not false-refuse.

    Built on a SYNTHETIC parser (brief §5), never on a real alias added to
    the shipped CLI: adding one there would be a genuine surface change and
    would move the golden, which is out of scope for this unit and would
    also make it impossible to tell "the fix works" apart from "the golden
    was regenerated".
    """

    @staticmethod
    def _synthetic_sub():
        import argparse

        parser = argparse.ArgumentParser(prog="synthetic")
        sub = parser.add_subparsers(dest="verb")
        sub.add_parser("real", aliases=["alias1"], help="real help text")
        sub.add_parser("other", help="other help text")
        return sub

    def test_alias_resolves_instead_of_raising(self):
        """The defect (measured by hand before this fix): `_verb_actions_by_identity`
        raised `MissingChoicesActionsError` naming `alias1` — a false refusal
        on an ordinary argparse feature, not a broken naming assumption.
        """
        from tests.cli_surface import _verb_actions_by_identity

        by_name = _verb_actions_by_identity(self._synthetic_sub())
        assert set(by_name) == {"real", "alias1", "other"}

    def test_alias_carries_the_primary_verbs_own_help_record(self):
        """Sub-decision (brief §2): an alias's `verb_action` is BYTE-IDENTICAL
        to its primary's, because `sub.choices["real"] is
        sub.choices["alias1"]` — argparse genuinely shares one parser object
        between them, so a distinct "alias_of" pointer would encode
        information argparse itself does not track.
        """
        from tests.cli_surface import _serialize_action, _verb_actions_by_identity

        by_name = _verb_actions_by_identity(self._synthetic_sub())
        assert by_name["real"] is by_name["alias1"]
        assert _serialize_action(by_name["real"]) == _serialize_action(by_name["alias1"])
        assert _serialize_action(by_name["real"])["help"] == "real help text"
        assert _serialize_action(by_name["real"])["metavar"] == "real (alias1)"

    def test_reverting_the_fix_raises_on_the_alias(self):
        """Oracle: simulate the pre-fix `dest == name` matcher directly, on
        the SAME synthetic parser, and show it still refuses — pinning that
        this test would have caught the regression before the fix landed.
        """
        from tests.cli_surface import MissingChoicesActionsError

        sub = self._synthetic_sub()
        choices_actions = sub._choices_actions
        by_dest = {a.dest: a for a in choices_actions}  # the CB-168 defect, verbatim
        missing = sorted(set(sub.choices) - set(by_dest))
        assert missing == ["alias1"]
        with pytest.raises(MissingChoicesActionsError):
            if missing:
                raise MissingChoicesActionsError(
                    f"no pseudo-action in sub._choices_actions matches verb(s) {missing} "
                    "by `.dest`"
                )


class TestDriftMessageNamesEveryKey:
    """CB-169: the detail loop must name a drifted key, never print an empty body."""

    def test_verb_action_only_drift_is_named(self):
        """The defect (measured by hand before this fix): a `verb_action`-only
        drift produced a "drifted:" header with NOTHING under it — the
        detail loop only ever compared `actions` by name, so a golden that
        added `verb_action` (Т-64, CB-152) drifted invisibly to the loop.
        """
        expected = {
            "foo": {
                "actions": [{"dest": "x"}],
                "has_handler": True,
                "verb_action": {"help": "old help"},
            }
        }
        current = {
            "foo": {
                "actions": [{"dest": "x"}],
                "has_handler": True,
                "verb_action": {"help": "NEW help"},
            }
        }
        assert current != expected
        msg = _drift_message(current, expected)
        assert "drifted:" in msg
        # The old behaviour: a header with nothing after it.
        assert not msg.rstrip().endswith("drifted:")
        assert "foo.verb_action" in msg
        assert "old help" in msg
        assert "NEW help" in msg

    def test_has_handler_only_drift_is_also_named(self):
        """A second key, to show the fix covers the UNION of keys rather than
        special-casing `verb_action` by name (brief §3 variant (b), rejected:
        that would just be a fourth enumerated form of the same defect)."""
        expected = {"foo": {"actions": [], "has_handler": True, "verb_action": {}}}
        current = {"foo": {"actions": [], "has_handler": False, "verb_action": {}}}
        msg = _drift_message(current, expected)
        assert "foo.has_handler: True -> False" in msg

    def test_reverting_the_fix_prints_an_empty_body(self):
        """Oracle: simulate the pre-fix `actions`-only detail loop directly
        over the same fixture, and show it produces the empty-body defect —
        pinning that this test would have caught the regression."""
        expected = {
            "foo": {
                "actions": [{"dest": "x"}],
                "has_handler": True,
                "verb_action": {"help": "old help"},
            }
        }
        current = {
            "foo": {
                "actions": [{"dest": "x"}],
                "has_handler": True,
                "verb_action": {"help": "NEW help"},
            }
        }
        drifted = sorted(v for v in current if current[v] != expected[v])
        lines = []
        for v in drifted:  # the CB-169 defect, verbatim: `actions`-only comparison
            cur_actions = current[v]["actions"]
            exp_actions = expected[v]["actions"]
            if len(cur_actions) != len(exp_actions):
                lines.append(f"  {v}: {len(exp_actions)} actions -> {len(cur_actions)}")
                continue
            for i, (ca, ea) in enumerate(zip(cur_actions, exp_actions)):
                if ca != ea:
                    for k in sorted(set(ca) | set(ea)):
                        if ca.get(k) != ea.get(k):
                            lines.append(f"  {v}[{i}].{k}: {ea.get(k)!r} -> {ca.get(k)!r}")
        detail = "\ndrifted:\n" + "\n".join(lines)
        assert detail.rstrip().endswith("drifted:"), (
            "premise: the pre-fix loop really does produce an empty body here"
        )
