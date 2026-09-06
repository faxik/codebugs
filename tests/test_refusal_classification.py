"""The classification of refusals has ONE source, and BOTH real boundaries follow it (CB-311).

WHAT THIS FILE GUARDS. `refusals.CLASSIFICATION` says, class by class, whether a
failure is a refusal a person is meant to read or a crash whose text stays in
the log. Before CB-311 that one rule was written out three times — inline in
`cli.domain_errors`, inline in `cli.main`'s outer arm, and as
`server._EXPECTED_REFUSALS` — and nothing compared them. They agreed by
coincidence, and CB-310's own comment named the missing half exactly: *"what is
missing is the enforcement, not the member"*. This file is that enforcement.

IT DRIVES THE BOUNDARIES, IT DOES NOT COMPARE LISTS. Checking that two tuples
hold the same classes would verify the ELEMENTS while saying nothing about their
COMPOSITION — the very defect being closed. So for every exception class in the
population, a probe verb and a probe tool are registered through the PRODUCTION
paths (`db.register_cli_provider` + `cli.main`; `server.build_registrar` +
`MCPServer.call_tool`), each raises exactly that class, and what the two
surfaces then DO is compared against what the table predicts.

THE PROBE VERB RAISES INSIDE `domain_errors()`, WHICH IS NOT AN ARBITRARY
CHOICE: it is what every real handler does, and it is what makes the CLI's TWO
arms observable through one shape. An `input` refusal is caught by the inner
arm; a `tracker` refusal passes straight through it — nothing in that region can
catch it — and is caught by `cli.main`'s outer arm; a crash passes both.

HOW A TRANSLATED REFUSAL IS RECOGNISED AT THE MCP BOUNDARY, AND WHY NOT BY THE
CLIENT'S TEXT. `pyproject.toml` admits `mcp>=2.0.0,<3`, and the two versions
differ exactly here: under 2.0.0 the SDK appends ANY exception's text to its own
message, so "the client can read the reason" is true even for a crash and cannot
discriminate anything; under 2.1.1 a crash arrives as a text-less
`UnexpectedToolError`. Both were measured on this tree rather than assumed. What
is stable across both is the TRANSLATION itself: `_RefusalsReachTheClient` raises
`ToolError(str(exc))`, so a translated refusal leaves an exception in the cause
chain whose message is EXACTLY the refusal's own text, while the SDK's own
wrapper always prefixes "Error executing tool <name>". That equality is the
predicate, and it is a statement about the refusal's text surviving — which is
the whole subject of CB-310.

THE TABLE'S OWN DISCIPLINE (CB-179) is checked here too: every row carries a
non-empty reason, every row names a class its module actually declares (a
renamed or deleted class cannot leave a stale row behind), and `RATIFIED` below
pins the composition so that DELETING a row turns this file red instead of
quietly narrowing both surfaces at once — a table that is both the behaviour and
its own expectation would agree with itself no matter what it said.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import pkgutil
import sys

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import codebugs
from codebugs import cli, db, refusals, server

# ---------------------------------------------------------------------------
# The ratified composition. This is NOT a second source of behaviour — nothing
# in `src/` reads it. It is the RATCHET, and WHAT IT ACTUALLY CATCHES IS A ROW
# BEING ADDED, not a row being removed.
#
# THE FIRST VERSION OF THIS COMMENT SAID "removing a row would narrow both
# surfaces silently", AND THAT WAS THE WEAKEST CASE RATHER THAN THE REAL ONE —
# the simplify pass went through all eight rows and found every deletion caught
# by something else: dropping `ValueError` or `json.JSONDecodeError` empties
# `CRASHES_INSIDE_REFUSALS` and reddens the arc-order test below; dropping
# `DeclarationError` or `WorktreeTrackerError` reddens the inheritance guard;
# dropping `KeyError`/`DatabaseNotFoundError`/`TrackerUnwritableError` reddens
# `tests/test_cb310_refusal_text.py`; dropping `TrackerExistsError` reddens
# `tests/test_db_infra.py`'s clean-refusal test. A justification a reader can
# check and find false gets the guard deleted as redundant, so it is corrected
# rather than left standing.
#
# AND YES, THIS DUPLICATES THE TABLE'S COMPOSITION ON PURPOSE. A later review
# read that as evidence the single source is not single. It is the opposite: an
# oracle written down INDEPENDENTLY is the only kind that can disagree with what
# it checks. Derive this from `refusals.CLASSIFICATION` and it becomes the table
# compared with itself — a tautology that passes whatever the table says, which
# is the first risk any reviewer names about tests like these. The duplication
# is the mechanism, not a leak.
#
# ADDING a row is the case nothing else sees. Put `TypeError: Classified(INPUT,
# ...)` in the table and both surfaces start handing a person the text of every
# `TypeError`; the behavioural checks below take their expectation from that same
# table and agree, the inheritance guard is satisfied (every package class is
# still named), and `test_cb310_refusal_text.py` only probes widening for
# `RuntimeError`. This dict is what turns red. Changing it is a deliberate,
# reviewable act — which is what "a ratified boundary" means.
# ---------------------------------------------------------------------------
RATIFIED: dict[str, str] = {
    "ValueError": refusals.INPUT,
    "KeyError": refusals.INPUT,
    "json.decoder.JSONDecodeError": refusals.CRASH,
    "codebugs.surfacegen.DeclarationError": refusals.INPUT,
    "codebugs.db.DatabaseNotFoundError": refusals.TRACKER,
    "codebugs.db.TrackerUnwritableError": refusals.TRACKER,
    "codebugs.db.TrackerExistsError": refusals.TRACKER,
    "codebugs.db.WorktreeTrackerError": refusals.TRACKER,
}

_MARKER = "cb311-probe-marker"


def _fqn(cls: type[BaseException]) -> str:
    if cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def _declared_exception_classes() -> dict[str, type[BaseException]]:
    """Every exception class DECLARED in this package, found by walking it.

    Mechanical enumeration rather than a list: a class added tomorrow is in the
    population without anyone remembering this file. `__module__ == module name`
    is what makes it "declared here" rather than "imported here", and it is also
    what gives the table its self-deletion — a class that is renamed or removed
    stops being found, and the row naming it fails.

    WHAT IT DOES NOT SEE, at the width it actually holds: it reads each module's
    top-level namespace, so a class declared INSIDE another class, or one that
    exists only as an element of a container, is invisible to it. Neither shape
    occurs in this package today (that is why the enumeration is honest now),
    and closing it would mean walking the AST rather than the namespace. Named
    rather than left implied, because "every exception class in the package" is
    the sentence a reader will carry away otherwise.
    """
    found: dict[str, type[BaseException]] = {}
    modules = [codebugs]
    for info in pkgutil.walk_packages(codebugs.__path__, prefix="codebugs."):
        modules.append(importlib.import_module(info.name))
    for module in modules:
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseException)
                and obj.__module__ == module.__name__
            ):
                found[_fqn(obj)] = obj
    return found


def _population() -> dict[str, type[BaseException]]:
    """What the boundaries are driven with: the package's own classes plus the
    three foreign ones the classification names, plus one control.

    `RuntimeError` is the control and is deliberately unnamed by the table: two
    of the tracker refusals descend from it, so a wrapper that widened to
    `RuntimeError` would translate everything, and a population without it could
    not tell that apart.
    """
    population = _declared_exception_classes()
    # DERIVED from the table, not listed beside it. A foreign class named by the
    # table — `json.JSONDecodeError` today, `TypeError` or `OSError` tomorrow —
    # changes what both surfaces do, so it must be driven through both. Listing
    # the foreign classes by hand left exactly that hole: a row could be added,
    # ratified in RATIFIED, alter the product, and never be exercised once.
    population.update({_fqn(cls): cls for cls in refusals.CLASSIFICATION})
    # `RuntimeError` is the control and is added SEPARATELY because it must stay
    # OUT of the table: two tracker refusals descend from it, so a wrapper that
    # widened to `RuntimeError` would translate everything, and a population
    # without it could not tell that apart.
    population[_fqn(RuntimeError)] = RuntimeError
    return population


#: Computed once. `_population()` walks the package and imports every submodule, and it is
#: read by the parametrize decorator AND by each of the three methods it parametrizes — 31
#: identical walks per run, measured at ~0.25 ms each once the modules are in `sys.modules`.
POPULATION: dict[str, type[BaseException]] = _population()


def _instantiate(cls: type[BaseException]) -> BaseException:
    """One instance of `cls` whose text is `_MARKER`, for ANY class in the population.

    A class whose constructor validates its argument cannot be built from an
    arbitrary string — `loc._Refused` refuses a token outside its closed
    vocabulary, deliberately and correctly. **Skipping such a class is the one
    thing this probe must not do**: the population is the whole point, and a
    class quietly missing from it is a classification nobody checked. So the
    fallback builds the instance without running that constructor and gives it
    the message through `BaseException.__init__`.

    What is knowingly given up, and why it costs nothing here: the instance may
    not satisfy its class's own invariants. Classification reads the TYPE and
    the TEXT, both of which are exactly right, and nothing downstream of a
    boundary arm inspects the object further.

    A SPECIAL CASE FOR `json.JSONDecodeError` USED TO STAND HERE and is gone:
    the fallback handles it (measured — its three-argument constructor rejects a
    lone string, the fallback runs, and the result is a real `JSONDecodeError`
    whose `str()` is the marker). Keeping it meant the ONE class this file's
    arc-order test turns on took a private path, so the general one was never
    exercised on a class that needs it — a special case layered on the very
    mechanism built to make special cases unnecessary.
    """
    try:
        return cls(_MARKER)
    except BaseException:  # noqa: BLE001 - a validating constructor is legitimate
        instance = cls.__new__(cls)
        BaseException.__init__(instance, _MARKER)
        return instance


# ---------------------------------------------------------------------------
# The two real boundaries.
# ---------------------------------------------------------------------------


#: The probe verb's own prefix, and it must NOT be `cli.main`'s outer arm's.
#: Both CLI arms end in one stderr line and exit 1, so an outcome of "refused"
#: alone cannot say WHICH arm caught it — and the two arms are the whole reason
#: the table distinguishes `input` from `tracker`. With the probe printing a
#: prefix the outer arm never prints, the arm becomes readable from the output,
#: and a mutant that folds every refusal into the inner arm turns red here
#: instead of only in someone else's end-to-end test.
_INNER_ARM_PREFIX = "cb311-inner-arm: "
_OUTER_ARM_PREFIX = "codebugs: "


@contextlib.contextmanager
def _probe_verb(exc: BaseException):
    """A CLI verb that raises `exc` the way every real handler raises: inside
    `domain_errors()`, around what stands in for the domain call."""

    def register_cli(sub, commands):
        parser = sub.add_parser("cb311-raise", help="CB-311 classification probe")
        parser.set_defaults(command="cb311-raise")

        def handler(_args):
            with cli.domain_errors(prefix=_INNER_ARM_PREFIX):
                raise exc

        commands["cb311-raise"] = handler

    original = db._cli_providers.copy()
    db.register_cli_provider("cb311_probe", register_cli)
    try:
        yield
    finally:
        db._cli_providers.clear()
        db._cli_providers.extend(original)


def cli_outcome(exc: BaseException, monkeypatch, capsys) -> tuple[str, str]:
    """Drive the REAL CLI entry point. Returns ("refused"|"crashed", stderr)."""
    with _probe_verb(exc):
        monkeypatch.setattr(sys, "argv", ["codebugs", "cb311-raise"])
        try:
            cli.main()
        except SystemExit as sysexit:
            assert sysexit.code == 1, f"a refusal must exit 1, got {sysexit.code}"
            return "refused", capsys.readouterr().err
        except BaseException:  # noqa: BLE001 - the crash arm is the observation
            return "crashed", capsys.readouterr().err
    raise AssertionError("the probe verb returned instead of raising")


def _causes(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def mcp_outcome(exc: BaseException) -> str:
    """Drive the REAL MCP surface, built through `server.build_registrar`."""
    mcp = MCPServer("cb311-classification-probe")
    registrar = server.build_registrar(mcp)

    def cb311_raise() -> dict:
        """CB-311 classification probe."""
        raise exc

    registrar.tool(name="cb311_raise")(cb311_raise)
    try:
        asyncio.run(mcp.call_tool("cb311_raise", {}))
    except BaseException as raised:  # noqa: BLE001 - both outcomes arrive this way
        translated = any(
            isinstance(link, ToolError) and str(link) == str(exc) for link in _causes(raised)
        )
        return "refused" if translated else "crashed"
    raise AssertionError("the probe tool returned instead of raising")


def _expected(cls: type[BaseException]) -> str:
    return "crashed" if refusals.kind_of(cls) == refusals.CRASH else "refused"


# ---------------------------------------------------------------------------
# The table's own discipline.
# ---------------------------------------------------------------------------


class TestTheTableIsDisciplined:
    def test_the_composition_is_the_ratified_one(self):
        assert {_fqn(cls): row.kind for cls, row in refusals.CLASSIFICATION.items()} == RATIFIED, (
            "the classification changed. That is a change to a ratified boundary, not a "
            "refactor: update RATIFIED in the same commit, and say in the message what "
            "moved and why."
        )

    def test_every_row_carries_a_reason(self):
        empty = [_fqn(cls) for cls, row in refusals.CLASSIFICATION.items() if not row.reason.strip()]
        assert not empty, (
            f"classification row(s) with no reason: {empty} -- a table whose rows can lose "
            "their justification becomes the place classes are parked silently."
        )

    def test_every_row_declares_a_recognised_kind(self):
        kinds = {refusals.INPUT, refusals.TRACKER, refusals.CRASH}
        wrong = {_fqn(cls): row.kind for cls, row in refusals.CLASSIFICATION.items()}
        assert all(kind in kinds for kind in wrong.values()), wrong

    def test_no_row_names_a_class_the_package_no_longer_declares(self):
        declared = _declared_exception_classes()
        stale = [
            _fqn(cls)
            for cls in refusals.CLASSIFICATION
            if cls.__module__.startswith("codebugs") and _fqn(cls) not in declared
        ]
        assert not stale, f"stale classification row(s): {stale}"

    def test_no_package_class_inherits_a_classification_silently(self):
        """A package exception descending from a refusal must be named on its own row.

        This is the hole the unit closes one level up: `WorktreeTrackerError`
        would otherwise be a refusal because its PARENT is, with nobody having
        decided that, and a `class X(ValueError)` added tomorrow would acquire a
        person-facing text the same way.
        """
        unnamed = [
            name
            for name, cls in _declared_exception_classes().items()
            if issubclass(cls, refusals.EXPECTED_REFUSALS) and cls not in refusals.CLASSIFICATION
        ]
        assert not unnamed, (
            f"exception class(es) inheriting a classification without a row: {unnamed} -- "
            "add a row to refusals.CLASSIFICATION naming the kind and the reason."
        )

    def test_the_derived_tuples_partition_the_table(self):
        assert set(refusals.EXPECTED_REFUSALS) == {
            cls for cls, row in refusals.CLASSIFICATION.items() if row.kind != refusals.CRASH
        }
        assert set(refusals.INPUT_REFUSALS).isdisjoint(refusals.TRACKER_REFUSALS)
        assert all(
            issubclass(cls, refusals.EXPECTED_REFUSALS) for cls in refusals.CRASHES_INSIDE_REFUSALS
        ), "a crash that descends from nothing expected needs no earlier arm"


# ---------------------------------------------------------------------------
# The boundaries agree with the table -- the composition, not the elements.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(POPULATION))
class TestBothSurfacesFollowTheTable:
    def test_the_cli_boundary(self, name, monkeypatch, capsys):
        cls = POPULATION[name]
        exc = _instantiate(cls)
        outcome, err = cli_outcome(exc, monkeypatch, capsys)
        assert outcome == _expected(cls), (
            f"{name}: the CLI boundary {outcome} where refusals.CLASSIFICATION says "
            f"{_expected(cls)}."
        )
        if outcome != "refused":
            return
        assert _MARKER in err and "Traceback" not in err
        # WHICH ARM CAUGHT IT, not merely that something did. `input` is what the
        # domain call raises about its arguments and is caught around that call;
        # `tracker` is raised while opening or creating the tracker and passes
        # straight through that region to `cli.main`'s outer arm. Asserting only
        # "refused" collapses the two, and the table's whole reason for having
        # two refusal kinds goes unchecked.
        expected_prefix = (
            _INNER_ARM_PREFIX if refusals.kind_of(cls) == refusals.INPUT else _OUTER_ARM_PREFIX
        )
        assert err.strip().startswith(expected_prefix), (
            f"{name}: expected the {refusals.kind_of(cls)} arm (prefix {expected_prefix!r}), "
            f"got {err.strip()[:60]!r}"
        )

    def test_the_mcp_boundary(self, name):
        cls = POPULATION[name]
        exc = _instantiate(cls)
        outcome = mcp_outcome(exc)
        assert outcome == _expected(cls), (
            f"{name}: the MCP boundary {outcome} where refusals.CLASSIFICATION says "
            f"{_expected(cls)}."
        )

    # THE THIRD TEST THAT USED TO STAND HERE IS GONE, AND WHY IS WORTH A NOTE.
    # It asserted "the two surfaces never disagree" by driving BOTH boundaries a
    # second time and comparing them to each other, and its docstring claimed it
    # could catch a mutant the two tests above somehow satisfy. That claim was
    # false by transitivity: both tests above compare their surface against the
    # SAME independently computed `_expected(cls)`, so two values equal to one
    # third value cannot differ. The simplify pass could construct no case where
    # it fired alone, so it bought a doubled run of both boundaries for every
    # class and nothing else — and a comment asserting a guarantee it does not
    # hold is the overclaim this repository keeps paying for.


class TestTheArcOrderIsPreservedAndDiscriminates:
    """`json.JSONDecodeError` IS a `ValueError`, and that is the whole point.

    This does not restate the order of two `except` clauses — it asserts the
    behaviour that the order exists to produce, on both surfaces, using a pair
    that differs ONLY by being a subclass.
    """

    def test_a_plain_value_error_is_refused_on_both_surfaces(self, monkeypatch, capsys):
        outcome, err = cli_outcome(ValueError(_MARKER), monkeypatch, capsys)
        assert outcome == "refused" and _MARKER in err
        assert mcp_outcome(ValueError(_MARKER)) == "refused"

    def test_its_subclass_json_decode_error_crashes_on_both_surfaces(self, monkeypatch, capsys):
        decode_error = json.JSONDecodeError(_MARKER, "{not json", 1)
        assert isinstance(decode_error, ValueError), "the premise of this test"
        outcome, _ = cli_outcome(decode_error, monkeypatch, capsys)
        assert outcome == "crashed", (
            "a post-commit serialization failure reported as bad input is the CB-16/CB-86 lie"
        )
        assert mcp_outcome(json.JSONDecodeError(_MARKER, "{not json", 1)) == "crashed"

    def test_the_earlier_arm_holds_exactly_the_crashes_that_need_it(self):
        assert json.JSONDecodeError in refusals.CRASHES_INSIDE_REFUSALS
