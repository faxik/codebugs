"""One implementation of "what does the CLI surface look like right now" (CB-146).

Both the golden generator (this module's own `__main__` block, run as
`PYTHONPATH=src uv run python tests/cli_surface.py > tests/golden/cli_surface.json`)
and the gate that compares against it (`tests/test_cli_surface.py`) import
`collect_cli_surface` from here, mirroring `tests/_mcp_schema.py`'s split for
the MCP wire golden — one drift-proof source rather than two copies that could
disagree about what the surface is.

WHAT IS SNAPSHOTTED, AND WHY GENERIC (brief §2). Every verb's every argparse
Action, as ALL of `vars(action)` reports it — not a curated list of "the
attributes that matter". Measured on 3.14.4: `vars(action)` returns TWELVE
keys. Eleven are pinned here: `dest`, `option_strings`, `nargs`, `default`,
`type`, `choices`, `required`, `help`, `metavar`, `const`, `deprecated`. The
twelfth, `container`, is excluded — see `_EXCLUDED_ACTION_ATTRS` below for why
that is a structural exclusion of one non-declarable key, not a curated
allowlist of the other eleven. A TWELFTH pinned key is added on top,
`action_class` (`type(action).__name__`, not one of argparse's own attributes)
— see `_serialize_action`'s docstring for the measured, non-contrived
collision it closes: `action="append"` and the default `action="store"`
produce byte-identical `vars()` whenever no other keyword differs.

Pinning every declared keyword rather than naming a subset is what makes this
snapshot catch `deprecated=True` (CB-148's measured symptom): a property
asserted over four named attributes (`required`/`choices`/`metavar`/`const`)
cannot see a fifth one arriving, because "checking elements" is not "checking
composition" — this repo's own recurring lesson. A property over `vars(action)`
has no fifth attribute to miss.

WHY BUILT THROUGH `cli.build_parser`, NOT BY CALLING `register_cli` DIRECTLY
(brief trap #1). `build_parser` is the exact function `cli.main()` calls to
build its own subparser tree — see its docstring in `src/codebugs/cli.py`. A
snapshot that instead looped over `db.get_cli_providers()` and called each
`register_fn` by hand would bypass `_register_builtins` and the provider loop
inside `cli.py` itself, and a wiring defect in either would be invisible to it.

WHY NO HARDCODED DOMAIN LIST (brief trap #2, and the card's main trap). This
module names no domain anywhere. `mode="all"` reaches every provider currently
in `db.get_cli_providers()` — the registry, not an enumeration — so a domain
that forgot to register, or one added tomorrow, changes this function's output
without anyone updating a list here.
"""

from __future__ import annotations

import argparse
from typing import Any

from codebugs import cli, db

# The ONE argparse Action attribute excluded from the snapshot, and the reason
# it is one attribute and not a curated set: `container` is a live
# back-reference to the `_ArgumentGroup` the action was attached to, set
# internally by argparse's own machinery (`_ActionsContainer._add_action`) —
# it is not something a declaration can pass as a keyword to `add_argument`,
# and its `repr` carries a memory address, so serializing it would make the
# golden fail on every single run regardless of any real surface change (brief
# trap #5: unstable serialization). Excluding it is a STRUCTURAL exclusion of
# the one non-declarable, non-deterministic key `vars(action)` returns — every
# other key, whatever argparse calls it, is captured.
_EXCLUDED_ACTION_ATTRS = frozenset({"container"})


class UnserializableArgumentType(Exception):
    """Raised loudly for a `type=` callable with no stable name (brief §3).

    An anonymous callable (`lambda`, a bare `functools.partial`) would
    otherwise fall back to `repr`, which carries a memory address — the golden
    would then fail on every run for a reason unrelated to the surface, and a
    ratchet that flaps for no reason is a ratchet operators learn to ignore.
    Refusing here, loudly, is cheaper than a flaky golden.
    """


def _serialize_type(type_obj: Any) -> str | None:
    """`type=` by NAME, not by object identity or `repr` (brief §3)."""
    if type_obj is None:
        return None
    name = getattr(type_obj, "__name__", None)
    # A lambda DOES have `__name__` — it is literally the string "<lambda>" —
    # so checking for *presence* of `__name__` would not catch it. A bare
    # `functools.partial` has no `__name__` at all, which `getattr` returns as
    # `None` here.
    if not name or name == "<lambda>":
        raise UnserializableArgumentType(
            f"argparse `type=` callable has no stable, non-anonymous name: {type_obj!r}. "
            "A CLI-surface snapshot cannot pin a lambda or a bare functools.partial "
            "without hardcoding a memory address into the golden — give it a `def`."
        )
    module = getattr(type_obj, "__module__", None)
    if module in (None, "builtins"):
        return name
    return f"{module}.{name}"


def _serialize_action(action: argparse.Action) -> dict[str, Any]:
    """Every attribute `vars(action)` reports, minus the one structural exclusion,
    PLUS the action's own CLASS NAME under `action_class`.

    Measured, not assumed: `action="append"` versus the default `store` action
    produce BYTE-IDENTICAL `vars(action)` (minus `container`) whenever no other
    keyword happens to differ — e.g. a plain `p.add_argument("--tags",
    help="...")` versus the same call with `action="append"` added. That is the
    CB-148 shape recurring inside this catcher's own remedy: `action=` is an
    argparse keyword that passes through untranslated (`surfacegen.emit_cli`,
    and every hand-written `register_cli`) and changes the user-visible surface
    — a repeatable flag accumulating into a list instead of overwriting — while
    being invisible to `vars(action)` alone, because argparse encodes `action=`
    as which Action SUBCLASS gets constructed, not as an attribute on the
    resulting instance. `type(action).__name__` is the one additional key that
    closes it; it is not a second curated attribute list, because it is the
    ONLY remaining discriminator `vars()` does not already expose.
    """
    out: dict[str, Any] = {"action_class": type(action).__name__}
    for key, value in vars(action).items():
        if key in _EXCLUDED_ACTION_ATTRS:
            continue
        if key == "type":
            value = _serialize_type(value)
        elif key == "choices" and value is not None:
            # Every `choices=` in this package is already a list/tuple (a
            # closed, ordered vocabulary), never a set — cast defensively so an
            # unordered choices container would fail LOUDLY at collection time
            # (a `TypeError`-free but non-deterministic dict/JSON round-trip)
            # rather than produce a golden that silently flaps between runs.
            value = list(value)
        out[key] = value
    return out


def collect_cli_surface(mode: str = "all") -> dict[str, Any]:
    """Every verb's every action, captured through the REAL `cli.py` path.

    Returns `{verb_name: {"actions": [...], "has_handler": bool}}`. `actions`
    is a LIST, in argparse's own insertion order — never sorted — so swapping
    two `add_argument` calls within one verb changes the snapshot (brief
    mutant #6); the top-level dict's key order does not matter, because the
    golden is dumped with `sort_keys=True`, which only reorders dict keys and
    never touches list element order.

    `has_handler` catches a verb registered into `sub` (visible in `--help`)
    whose entry into `commands` was skipped — a verb that would raise `KeyError`
    the moment a user actually invoked it despite looking wired.
    """
    db._ensure_modules_loaded()
    _parser, sub, commands = cli.build_parser(mode=mode)
    verbs: dict[str, Any] = {}
    for name, subparser in sub.choices.items():
        verbs[name] = {
            "actions": [_serialize_action(a) for a in subparser._actions],
            "has_handler": name in commands,
        }
    return verbs


if __name__ == "__main__":
    import json

    print(json.dumps(collect_cli_surface(), indent=2, sort_keys=True))
