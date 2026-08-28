"""Every function that BINDS a row limit into SQL validates it first (CB-208),
and an empty CLI page no longer states a property of the corpus when the truth
is a property of the request (CB-210).

WHY THIS FILE EXISTS, AND WHY IT CARRIES A GATE RATHER THAN EIGHT TESTS.
CB-161 built `types.require_row_limit` for three interpolating callers. CB-196
routed three BINDING callers through it and, on the way, swept for the rest of
the population and found it larger than the card said. CB-208 recorded that
remainder and then reproduced this repository's signature failure ON ITSELF:
its immutable heading said EIGHT, its body listed SEVEN, and the eighth
(`milestones.foundation.query_audit`) existed only in the card's notes, because
a finding's description cannot be rewritten. Three readers of the same card got
three different inventories.

The card names the reason no fourth reader would do better: **"Появление
девятого сайта ловится ревью или ничем"** — ruff's `S608` is not enabled here
(CB-172) and would not see these anyway, since BINDING is precisely what every
one of them does correctly. What is missing is validation, and nothing
mechanical looks for its absence. So the eight behavioural tests below are the
FIX, and `TestEveryBindingSiteIsGuarded` is the thing that keeps it fixed: a
ninth site turns this file red instead of waiting for a fourth inspection. The
model is `tests/test_no_network_capability.py` and
`tests/test_two_valued_path_gate.py` — a self-deleting `DECLARED_EXCEPTIONS`
table with a reason per row, plus a premise test proving the gate can see
anything at all, plus a mutation test proving it discriminates.

WHAT THE GATE DELIBERATELY DOES NOT COVER, said at the width it is actually
held and no wider. A SECOND shape rides along and is NOT this one: a limit
applied as a Python SLICE (`rows[:limit]`), where a negative value silently
trims the TAIL rather than removing the bound. Eight functions have that shape
today. What a negative limit should MEAN to a slice — a refusal, or zero rows —
is undecided, so folding them in here would smuggle an unratified behaviour
change into a validation fix (CB-82). `TestTheSliceClassIsOutOfScope`
pins that they are still unguarded, so the day somebody decides, this file
tells them the decision has not been taken rather than implying it has.

THE PROPERTY, STATED AT THE WIDTH IT IS ACTUALLY HELD. The gate holds one
sentence: *inside `src/codebugs/`, a function whose OWN body contains a string
literal spelling `LIMIT ?` or `LIMIT :name` either calls `require_row_limit` or
is declared with a reason.* Everything below is a way out of that sentence, and
every one of them was RUN against the predicate rather than reasoned about — a
gate whose promise is wider than its check is the defect this file exists to
close, one level up, so the misses are enumerated instead of implied. A miss
that is announced costs less than a miss that is not.

MEASURED ESCAPES — the gate does NOT see these, and each was reproduced:

  1. THE SQL TEXT IS NOT A LITERAL IN THIS FUNCTION. A module-level constant
     (`SQL = "... LIMIT ?"` executed by a helper), a string arriving as a
     PARAMETER, or SQL assembled in a sibling module and merely executed here.
     Verified absent from the tree today by a separate sweep of module-level
     constants, but nothing stops the next one.
  2. INTERPOLATION RATHER THAN BINDING — `f"LIMIT {n}"`, `"LIMIT %s" % n`.
     That is CB-161's original class, not this one: those sites never bind, so
     "binds a limit" is false of them and this predicate is the wrong tool. It
     is named here so nobody reads this gate as covering all limit defects.
  3. THE LIMIT REACHES SQL FROM SOMEWHERE OTHER THAN A PARAMETER — read off an
     object attribute, a dict, or module state. The gate never looks at where
     the bound VALUE came from; it only asks whether the function validated.

A CLAIMED ESCAPE THAT TURNED OUT NOT TO BE ONE, kept because the correction is
the point. This list first said an explicit `"... LIMIT" + " ?"` escaped,
splitting the token across two `ast.Constant` nodes. Running it showed the
opposite: the predicate joins every string constant in a body with a NEWLINE
before matching, and `\\s+` spans that, so the two halves read as adjacent and
the site IS caught. The prose was wrong in the GENEROUS direction — claiming
less coverage than exists is harmless, but it is still a claim nobody had run.
Note the same join is why an unrelated `"...LIMIT"` and a `"?"` sitting
elsewhere in one body could FALSE-POSITIVE; that is the safe direction.

KNOWN FALSE POSITIVES, which are the SAFE direction and are left unfixed
deliberately: a guard reached through `getattr`, through a local helper, or
written as an equivalent inline `if limit < 0: raise` is not recognised as a
guard, so such a function is FLAGGED. That costs a loud, easily-answered test
failure rather than a silent hole, and closing it would mean value tracking —
the same boundary `test_no_network_capability.py` draws around `__import__`.

WHAT IS COVERED, also measured rather than assumed: lower-case and mixed-case
keywords (SQLite accepts `limit ?`, and a case-SENSITIVE predicate let that
walk straight past — found by running the battery, not by reading the regex),
`LIMIT :name` as well as `LIMIT ?`, a newline between keyword and placeholder,
methods inside a class, `async def`, a nested function that binds on its own,
and the three spellings of the guard call (`require_row_limit`,
`t.require_row_limit`, `types.require_row_limit`) — resolved by ATTRIBUTE name
so they are ONE capability rather than three strings to enumerate, which
matters because `claims.py` really does use the middle one.
"""

import ast
import functools
import re
import subprocess
import sys
from pathlib import Path

import pytest

from codebugs import blockers, claims, db, findings, usage
from codebugs.milestones import foundation

SRC = Path(__file__).resolve().parent.parent / "src" / "codebugs"


# Fixtures are defined HERE, not in `conftest.py`: this project admits exactly
# one kind of inhabitant into the shared file, and an ordinary fixture is not
# it (CLAUDE.md, Testing).
@pytest.fixture
def tmp_project(tmp_path):
    """A temporary project directory with an initialized tracker."""
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    """A connected database on that tracker."""
    c = db.connect(tmp_project)
    yield c
    c.close()

# A function that binds a row limit into SQL and does NOT validate it. The
# table is EMPTY on purpose: every site the sweep found is fixed, and a row
# here is a promise that some future reader will have to justify in words.
# It is SELF-DELETING — a row naming a function that no longer matches the
# predicate fails, so this cannot rot into the place real regressions are
# parked, which is the hole the gate exists to close, one level up.
DECLARED_EXCEPTIONS: dict[tuple[str, str], str] = {}

# CASE-INSENSITIVE, and that is a measured fix rather than a precaution: SQLite
# accepts `select 1 limit ?` exactly as it accepts the shouted form, so a
# case-sensitive predicate let a lower-case site walk straight past the gate.
# Caught by running the evasion battery in this file's own mutation test rather
# than by reading the regex. `\s+` spans a newline, so a query broken across
# source lines between the keyword and its placeholder is still seen.
_BINDS_A_LIMIT = re.compile(r"LIMIT\s+[?:]", re.IGNORECASE)


def _own_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Nodes belonging to `fn` itself: docstring dropped, nested functions excluded.

    The docstring is dropped because `require_row_limit`'s own docstring quotes
    `LIMIT ?` while discussing the rule, and prose about a defect is not the
    defect. Nested functions are excluded so a guard written in an inner
    closure cannot vouch for its enclosing function or the reverse -- the unit
    that must validate is the unit that binds.
    """
    nested = {
        id(m)
        for inner in ast.walk(fn)
        if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef) and inner is not fn
        for m in ast.walk(inner)
    }
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return [n for stmt in body for n in ast.walk(stmt) if id(n) not in nested]


def _unguarded_binding_sites(source: str, module: str) -> list[tuple[str, str]]:
    """(module, function) for every function that binds a limit without validating it."""
    out: list[tuple[str, str]] = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        nodes = _own_nodes(fn)
        text = "\n".join(
            n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        if not _BINDS_A_LIMIT.search(text):
            continue
        called = {
            (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
            for n in nodes
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name | ast.Attribute)
        }
        # Resolved by ATTRIBUTE name, not by the full dotted spelling, so
        # `require_row_limit(...)`, `t.require_row_limit(...)` and
        # `types.require_row_limit(...)` are one capability rather than three
        # strings to enumerate -- `claims.py` really does use the second form.
        if "require_row_limit" not in called:
            out.append((module, fn.name))
    return out


@functools.lru_cache(maxsize=1)
def _package_sources() -> tuple[tuple[str, str], ...]:
    """(module, source) for every package module, read once per session.

    Eight tests in this file sweep the whole tree, and without this each one
    re-read and re-parsed ~40 files. Cached at the SOURCE level rather than at
    the result level, because the two sweeps here want different things from
    the same bytes (the guard predicate, and the slice-class predicate) and one
    of them feeds a mutated copy to the predicate.
    """
    out = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((str(path.relative_to(SRC)), path.read_text()))
    return tuple(out)


def _all_unguarded() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for module, source in _package_sources():
        found.extend(_unguarded_binding_sites(source, module))
    return found


class TestEveryBindingSiteIsGuarded:
    """The gate. A ninth site turns this red instead of shipping."""

    def test_no_undeclared_binding_site_skips_validation(self):
        undeclared = [s for s in _all_unguarded() if s not in DECLARED_EXCEPTIONS]
        assert undeclared == [], (
            "These functions bind a row limit into SQL without calling "
            "`types.require_row_limit`, so SQLite reads a negative value as NO "
            "limit and the caller silently receives the whole table. Route the "
            "value through the guard by ASSIGNMENT at the top of the body "
            "(`limit = require_row_limit('limit', limit)`), never as a separate "
            "check beside the binding -- a check beside the binding has to be "
            "re-established every time a statement is inserted between the two "
            f"(CB-41). Offenders: {undeclared}"
        )

    def test_the_exceptions_table_is_self_deleting(self):
        """A row naming a function that no longer matches must fail.

        Without this the table becomes the place real regressions are parked,
        which is the defect the gate closes, one level up.
        """
        stale = [row for row in DECLARED_EXCEPTIONS if row not in _all_unguarded()]
        assert stale == [], (
            f"These DECLARED_EXCEPTIONS rows no longer describe anything real "
            f"-- delete them: {stale}"
        )

    def test_premise_the_gate_can_see_the_source_at_all(self):
        """A gate that reads nothing reports clean. Prove it reads something.

        This asserts the gate finds the GUARDED population, not the unguarded
        one: the healthy state of the tree is zero offenders, so "found
        nothing" is indistinguishable from "looked at nothing" without this.
        """
        guarded = 0
        for _module, text in _package_sources():
            if _BINDS_A_LIMIT.search(text) and "require_row_limit" in text:
                guarded += 1
        assert guarded >= 5, f"only {guarded} files bind a limit AND validate it -- gate is blind"

    def test_the_gate_discriminates_an_unguarded_site(self):
        """Mutation: a synthetic offender must be caught, a guarded twin must not.

        A predicate that both accepts and rejects the same input proves nothing
        about either, so the two halves are asserted together.
        """
        offender = (
            "def f(conn, limit):\n"
            '    return conn.execute("SELECT * FROM t LIMIT ?", [limit]).fetchall()\n'
        )
        guarded = (
            "def f(conn, limit):\n"
            '    limit = require_row_limit("limit", limit)\n'
            '    return conn.execute("SELECT * FROM t LIMIT ?", [limit]).fetchall()\n'
        )
        assert _unguarded_binding_sites(offender, "m.py") == [("m.py", "f")]
        assert _unguarded_binding_sites(guarded, "m.py") == []

    def test_the_gate_ignores_a_limit_that_is_only_discussed_in_prose(self):
        """`require_row_limit`'s own docstring quotes `LIMIT ?`. Prose is not a bind."""
        prose = 'def f(conn, limit):\n    """A note about LIMIT ? and why it matters."""\n    return 1\n'
        assert _unguarded_binding_sites(prose, "m.py") == []

    def test_the_gate_sees_a_named_placeholder_too(self):
        """`claims.list_claims` binds `LIMIT :limit`, not `LIMIT ?`."""
        named = (
            "def f(conn, limit):\n"
            '    return conn.execute("SELECT * FROM t LIMIT :limit", {"limit": limit})\n'
        )
        assert _unguarded_binding_sites(named, "m.py") == [("m.py", "f")]


# The eight this unit fixed. Used ONLY by the two-sided mutation test below,
# which strips the guard calls out of the real tree IN MEMORY and asserts the
# gate names exactly these. It is not a second inventory the gate consults --
# the gate itself holds no list.
_THE_EIGHT = {
    ("blockers.py", "query_deferred_entities"),
    ("claims.py", "list_claims"),
    ("findings.py", "anchor_candidates"),
    ("findings.py", "grouping_candidates"),
    ("findings.py", "recent_findings"),
    ("findings.py", "similarity_candidates"),
    ("milestones/foundation.py", "query_audit"),
    ("usage.py", "usage_summary"),
}

# The six that ALREADY routed through the guard before this unit -- three from
# CB-161 (the interpolation class) and three from CB-196. Stripping the guard
# out of the whole tree unguards these too, so the mutation below must expect
# fourteen and not eight. Writing only the eight made the test fail the first
# time it ran, which is the test working: the union is the real population of
# "functions that bind a row limit", and the split between the two sets is the
# history of how it came to be guarded.
_ALREADY_GUARDED_BEFORE_THIS_UNIT = {
    ("bench.py", "list_runs"),
    ("bench.py", "query"),
    ("findings.py", "query_findings"),
    ("reqs.py", "query_requirements"),
    ("sweep.py", "list_items"),
    ("sweep.py", "next_batch"),
}


class TestTheGateIsTwoSidedOnTheRealTree:
    """Silence on a healthy tree is not evidence; red on a mutated one is.

    `test_no_undeclared_binding_site_skips_validation` passing means the gate
    found nothing -- which is also exactly what a BLIND gate reports. The
    premise test above rules out one way of being blind (it can read files at
    all); this class rules out the other by MUTATING THE REAL SOURCE in memory:
    strip every `require_row_limit(...)` call out of the tree, and the gate must
    then name the eight functions this unit fixed, no more and no fewer. Both
    directions are asserted here together, because a predicate that only ever
    answers one way is evidence for neither answer.

    Nothing is written to disk -- the mutation is textual and in memory.
    """

    @staticmethod
    def _strip_guard(source: str) -> str:
        """Remove the guard call while leaving the binding intact.

        `limit = require_row_limit("limit", limit)` becomes `limit = limit`: a
        no-op that keeps every following line valid, so the function still
        parses and still binds its limit. A `t.` or `types.` prefix is matched
        by an optional attribute head.
        """
        return re.sub(
            r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?require_row_limit\("
            r"\s*[^,]+,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            r"\1",
            source,
        )

    def _unguarded_after_stripping(self) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for module, source in _package_sources():
            found.update(_unguarded_binding_sites(self._strip_guard(source), module))
        return found

    def test_premise_the_mutation_actually_removes_the_guard(self):
        """If the stripper matched nothing, the red half below proves nothing."""
        original = (SRC / "findings.py").read_text()
        mutated = self._strip_guard(original)
        assert "require_row_limit(" in original, "premise: findings.py calls the guard"
        assert "require_row_limit(" not in mutated, "the stripper did not remove the call"
        # Still valid Python, or the gate would be reacting to a SyntaxError
        # rather than to the property under test.
        ast.parse(mutated)

    def test_stripping_every_guard_names_the_whole_binding_population(self):
        assert self._unguarded_after_stripping() == (
            _THE_EIGHT | _ALREADY_GUARDED_BEFORE_THIS_UNIT
        )

    def test_the_eight_this_unit_fixed_are_among_them(self):
        """The half that speaks to THIS card, separated from the inherited six."""
        assert _THE_EIGHT <= self._unguarded_after_stripping()
        assert not (_THE_EIGHT & _ALREADY_GUARDED_BEFORE_THIS_UNIT), (
            "the two sets must stay disjoint -- a function cannot be both"
        )

    def test_and_the_unmutated_tree_is_silent(self):
        """The other side of the same claim, deliberately in the same class."""
        assert set(_all_unguarded()) == set()


class TestTheGateAgainstAnEvasionBattery:
    """Every coverage claim in this module's docstring, as a running test.

    A docstring listing what a gate catches is prose until something runs it.
    The LOWER-CASE row is here because it was a genuine, unnoticed hole: SQLite
    accepts `select 1 limit ?`, the first version of this gate was
    case-sensitive, and only running the battery found it.
    """

    CAUGHT = {
        "plain": 'def f(c, limit):\n    return c.execute("SELECT 1 LIMIT ?", [limit])\n',
        "named placeholder": (
            'def f(c, limit):\n    return c.execute("SELECT 1 LIMIT :limit", {"limit": limit})\n'
        ),
        "lower case": 'def f(c, limit):\n    return c.execute("select 1 limit ?", [limit])\n',
        "mixed case": 'def f(c, limit):\n    return c.execute("SELECT 1 LiMiT ?", [limit])\n',
        "newline between": 'def f(c, limit):\n    return c.execute("SELECT 1 LIMIT\\n?", [limit])\n',
        "built with +=": (
            'def f(c, limit):\n    s = "SELECT 1 "\n    s += "LIMIT ?"\n'
            "    return c.execute(s, [limit])\n"
        ),
        "implicit concat": (
            'def f(c, limit):\n    return c.execute("SELECT 1 "\n        "LIMIT ?", [limit])\n'
        ),
        "method in a class": (
            'class K:\n    def f(self, c, limit):\n        return c.execute("SELECT 1 LIMIT ?", [limit])\n'
        ),
        "async def": 'async def f(c, limit):\n    return c.execute("SELECT 1 LIMIT ?", [limit])\n',
        "explicit + across two constants": (
            'def f(c, limit):\n    return c.execute("SELECT 1 LIMIT" + " ?", [limit])\n'
        ),
    }

    ESCAPES = {
        "module-level SQL constant": (
            'SQL = "SELECT 1 LIMIT ?"\ndef f(c, limit):\n    return c.execute(SQL, [limit])\n'
        ),
        "interpolated, never bound": (
            'def f(c, limit):\n    return c.execute(f"SELECT 1 LIMIT {limit}")\n'
        ),
        "percent formatting": (
            'def f(c, limit):\n    return c.execute("SELECT 1 LIMIT %s" % limit)\n'
        ),
        "SQL arrives as a parameter": (
            "def f(c, sql, limit):\n    return c.execute(sql, [limit])\n"
        ),
    }

    QUIET = {
        "guarded, bare name": (
            'def f(c, limit):\n    limit = require_row_limit("limit", limit)\n'
            '    return c.execute("SELECT 1 LIMIT ?", [limit])\n'
        ),
        "guarded, t. prefix": (
            'def f(c, limit):\n    limit = t.require_row_limit("limit", limit)\n'
            '    return c.execute("SELECT 1 LIMIT ?", [limit])\n'
        ),
        "guarded, types. prefix": (
            'def f(c, limit):\n    limit = types.require_row_limit("limit", limit)\n'
            '    return c.execute("SELECT 1 LIMIT ?", [limit])\n'
        ),
        "prose only": 'def f(c, limit):\n    """Discusses LIMIT ? in prose."""\n    return 1\n',
    }

    @pytest.mark.parametrize("name", sorted(CAUGHT))
    def test_these_are_caught(self, name):
        assert _unguarded_binding_sites(self.CAUGHT[name], "m.py") == [("m.py", "f")], (
            f"{name} escaped the gate"
        )

    def test_a_nested_function_that_binds_is_caught_as_itself(self):
        """Attributed to the INNER function -- the unit that binds is the unit judged."""
        src = (
            "def outer(c, limit):\n"
            "    def inner():\n"
            '        return c.execute("SELECT 1 LIMIT ?", [limit])\n'
            "    return inner()\n"
        )
        assert _unguarded_binding_sites(src, "m.py") == [("m.py", "inner")]

    @pytest.mark.parametrize("name", sorted(ESCAPES))
    def test_these_escape_and_the_docstring_says_so(self, name):
        """PIN OF A KNOWN GAP. A failure here means the gate got BETTER.

        If one of these starts being caught, that is good news, and this test is
        what tells you to update the docstring's escape list -- rather than
        letting it rot into a claim that is quietly false in the generous
        direction, which is the failure this whole file exists to prevent.
        """
        assert _unguarded_binding_sites(self.ESCAPES[name], "m.py") == [], (
            f"{name} is now caught -- update the module docstring's escape list"
        )

    @pytest.mark.parametrize("name", sorted(QUIET))
    def test_these_raise_no_alarm(self, name):
        assert _unguarded_binding_sites(self.QUIET[name], "m.py") == []


class TestTheEightSitesRefuseANegativeLimit:
    """CB-208's population, one test each. Every one returned everything before.

    Measured on the unfixed tree with a five-row throwaway tracker: all eight
    returned rows at exit 0; after the fix all eight raise. The two reachable
    from a user surface are the sharp half -- `recent` sat beside `query` over
    the SAME table answering the same argument the opposite way, and
    `milestone-audit` is reachable from both the CLI and MCP.
    """

    @staticmethod
    def _seed(conn):
        for i in range(3):
            findings.add_finding(
                conn,
                severity="low",
                category="bug",
                file=f"f{i}.py",
                description=f"row limit gate seed finding number {i}",
                new_category=(i == 0),
            )

    def test_recent_findings(self, conn):
        self._seed(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            findings.recent_findings(conn, since="2020-01-01", limit=-1)

    def test_similarity_candidates(self, conn):
        self._seed(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            findings.similarity_candidates(conn, limit=-1)

    def test_grouping_candidates(self, conn):
        self._seed(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            findings.grouping_candidates(conn, limit=-1)

    def test_anchor_candidates(self, conn):
        self._seed(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            findings.anchor_candidates(conn, limit=-1)

    def test_query_deferred_entities(self, conn):
        with pytest.raises(ValueError, match="must not be negative"):
            blockers.query_deferred_entities(conn, "finding", -1)

    def test_list_claims(self, conn):
        with pytest.raises(ValueError, match="must not be negative"):
            claims.list_claims(conn, limit=-1)

    def test_usage_summary(self, conn):
        with pytest.raises(ValueError, match="must not be negative"):
            usage.usage_summary(conn, limit=-1)

    def test_query_audit(self, conn):
        with pytest.raises(ValueError, match="must not be negative"):
            foundation.query_audit(conn, limit=-1)

    def test_the_refusal_happens_before_any_row_is_read(self, conn):
        """The guard is the FIRST statement, so a refusal costs no partial work.

        `query_deferred_entities` is the discriminating site: it has an early
        return that reports `limit` back to the caller, so a guard placed after
        the blocker evaluation would let a negative value travel out inside a
        success-shaped response instead of raising.
        """
        self._seed(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            blockers.query_deferred_entities(conn, "finding", -1)


class TestNoFalseRefusal:
    """The cost of a wrong guard falls on everyone, not on the author.

    `None` means "no limit" on the four sites declared `int | None` and must
    keep working; zero means zero rows and is legal by CB-161's ratified
    decision; the ordinary positive path must be untouched. A sweep of every
    call site in `src/`, `tests/` and `tools/` found no caller anywhere passing
    a negative value, so none of these guards can refuse existing work.
    """

    def test_none_still_means_no_limit(self, conn):
        for i in range(3):
            findings.add_finding(
                conn,
                severity="low",
                category="bug",
                file=f"n{i}.py",
                description=f"no false refusal seed number {i}",
                new_category=(i == 0),
            )
        assert len(findings.similarity_candidates(conn, limit=None)) == 3
        assert len(findings.grouping_candidates(conn, limit=None)) == 3
        assert len(findings.anchor_candidates(conn, limit=None)) == 3
        assert usage.usage_summary(conn, limit=None)["rows"] == []

    def test_zero_still_means_zero_rows(self, conn):
        findings.add_finding(
            conn,
            severity="low",
            category="bug",
            file="z.py",
            description="zero still means zero rows on the recent verb",
            new_category=True,
        )
        assert findings.recent_findings(conn, since="2020-01-01", limit=0)["findings"] == []

    def test_a_positive_limit_still_truncates(self, conn):
        for i in range(3):
            findings.add_finding(
                conn,
                severity="low",
                category="bug",
                file=f"p{i}.py",
                description=f"positive limit still truncates seed number {i}",
                new_category=(i == 0),
            )
        assert len(findings.recent_findings(conn, since="2020-01-01", limit=2)["findings"]) == 2

    def test_the_defaults_still_work(self, conn):
        """Every one of the eight is reachable with no limit argument at all."""
        assert claims.list_claims(conn)["claims"] == []
        assert foundation.query_audit(conn) == []
        assert usage.usage_summary(conn)["rows"] == []
        assert blockers.query_deferred_entities(conn, "finding")["total"] == 0


class TestTheSliceClassIsOutOfScope:
    """A limit applied as `rows[:limit]` is a DIFFERENT defect with the same input.

    A negative value there does not remove the bound, it drops the TAIL -- so
    `triage_inbox(limit=-1)` silently returns the queue minus its last row.
    What that should mean is undecided, and deciding it inside a validation fix
    would be an unratified behaviour change (CB-82). This test pins that the
    decision has NOT been taken, so the next reader is told the truth rather
    than inferring from silence that the class is covered.
    """

    SLICE_SITES = {
        ("embeddings.py", "search_similar"),
        ("grouping.py", "citation_report"),
        ("grouping.py", "tag_report"),
        ("grouping.py", "filing_report"),
        ("milestones/foundation.py", "list_milestone_items"),
        ("milestones/triage.py", "triage_inbox"),
        ("similarity.py", "find_similar"),
        ("similarity.py", "group_report"),
    }

    def _slice_sites(self) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for module, source in _package_sources():
            for fn in ast.walk(ast.parse(source)):
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for n in _own_nodes(fn):
                    if (
                        isinstance(n, ast.Subscript)
                        and isinstance(n.slice, ast.Slice)
                        and isinstance(n.slice.upper, ast.Name)
                        and "limit" in n.slice.upper.id.lower()
                    ):
                        found.add((module, fn.name))
        return found

    def test_the_slice_population_is_unchanged(self):
        """If this fails, someone changed the slice class -- deliberately or not."""
        assert self._slice_sites() == self.SLICE_SITES

    def test_the_slice_sites_are_still_unvalidated(self):
        """Pin of a KNOWN GAP, not of correct behaviour. Read the class docstring."""
        unguarded = {(m, f) for m, f in _all_unguarded()}
        # None of the slice sites acquired a SQL-binding guard, because none of
        # them binds; the point is that this file has not quietly grown a
        # verdict about them.
        assert not (self._slice_sites() & unguarded)


class TestZeroLimitTellsTheTruthOnTheCli:
    """CB-210 -- `(no findings match)` states a property of the CORPUS.

    Over a full tracker asked for zero rows that sentence is simply false, and
    the MCP surface of the same verb has always been honest because it returns
    `total`. The number was computed, sitting in the same dict, and thrown away
    by a bare `return` on the empty branch.

    THE FIX IS DELIBERATELY NARROWER THAN THE CARD PROPOSED. The card suggested
    falling through to the existing total line, but the empty branch is shared
    by EVERY empty page, so that would move a hot verb's output for every user
    whose query genuinely matched nothing. The message changes only when the
    emptiness COULD be the request's doing -- limit zero AND something actually
    matched -- and a genuinely empty result keeps its byte-identical text. The
    second half of that condition is not decoration: with `--ids` naming rows
    that do not exist, `query_findings` raises the limit to fit the id list, so
    the emptiness is the corpus's doing and `total` is 0 there.
    """

    def _run(self, project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    @pytest.fixture
    def project(self, tmp_project):
        conn = db.connect(tmp_project)
        for i in range(3):
            findings.add_finding(
                conn,
                severity="low",
                category="bug",
                file=f"c{i}.py",
                description=f"cb210 corpus seed finding number {i}",
                new_category=(i == 0),
            )
        conn.close()
        return tmp_project

    def test_query_names_the_request_not_the_corpus(self, project):
        r = self._run(project, "query", "--limit", "0")
        assert r.returncode == 0, r.stderr
        assert "limit was 0" in r.stdout
        assert "3 finding(s) match" in r.stdout
        assert "(no findings match)" not in r.stdout

    def test_a_genuinely_empty_result_is_byte_identical(self, project):
        """The control. This text must NOT move -- it is a hot verb's output."""
        r = self._run(project, "query", "--status", "wont_fix")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "(no findings match)\n"

    def test_an_empty_tracker_with_limit_zero_keeps_the_old_text(self, tmp_project):
        """`total` is 0, so the emptiness is the corpus's doing after all.

        This is the half of the condition that keeps the message honest in the
        other direction: saying "you asked for zero rows" over a tracker that
        has nothing anyway would trade one false statement for another.
        """
        r = self._run(tmp_project, "query", "--limit", "0")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "(no findings match)\n"

    def test_reqs_query_names_the_request(self, project):
        conn = db.connect(project)
        from codebugs import reqs

        reqs.add_requirement(conn, req_id="R-1", description="a requirement for the cb210 probe")
        conn.close()
        r = self._run(project, "reqs-query", "--limit", "0")
        assert r.returncode == 0, r.stderr
        assert "limit was 0" in r.stdout
        assert "1 requirement(s) match" in r.stdout

    def test_reqs_query_genuinely_empty_is_byte_identical(self, tmp_project):
        r = self._run(tmp_project, "reqs-query")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "(no requirements match)\n"

    def test_sweep_next_names_the_request(self, project):
        self._run(project, "sweep-create", "--name", "s1")
        self._run(project, "sweep-add", "s1", "a.py", "b.py")
        r = self._run(project, "sweep-next", "s1", "--limit", "0")
        assert r.returncode == 0, r.stderr
        assert "limit was 0" in r.stdout
        assert "remaining" in r.stdout

    def test_sweep_next_genuinely_empty_is_byte_identical(self, project):
        self._run(project, "sweep-create", "--name", "s2")
        r = self._run(project, "sweep-next", "s2")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "(no unprocessed items)\n"


class TestTheSurfacesNameTheContractTheyEnforce:
    """CB-208's second half, recorded on the card by T-93's acceptor.

    T-93 brought three neighbouring verbs' help text to a formulation naming
    the new contract and left `recent`'s bare at `Max results (default 100)`,
    so a reader of `recent --help` alone would see an argument that LOOKS like
    its neighbours' and get the opposite behaviour. The card states the cost in
    words: otherwise the divergence just moves from behaviour into
    documentation.

    THE ASSERTION IS WIDENED FROM `recent` TO EVERY SURFACE THIS CHANGE
    TOUCHED, and that is the card's intent rather than its letter. Fixing the
    one site the card happens to name, while `milestone-audit`, `usage`,
    `anchor-resolve` and `anchor-recapture` began refusing negatives with no
    text saying so, would be the same defect with a different verb in it.
    """

    def _cli_help(self, verb: str, dest: str) -> str:
        """Read the help through the SANCTIONED collector, not a second walk of argparse.

        `tests/cli_surface.py::collect_cli_surface` already traverses
        `build_parser()` -> `sub.choices` -> `subparser._actions`, and its own
        docstring says why it exists: "one drift-proof source rather than two
        copies that could disagree about what the surface is". A private walk
        here would be exactly that second copy — it would keep working while the
        collector was hardened around it, and the two would answer differently
        about what the CLI surface is. The MCP half of this class already
        imports `collect_tool_schemas` for the same reason.
        """
        from tests.cli_surface import collect_cli_surface

        actions = collect_cli_surface()[verb]["actions"]
        for action in actions:
            if action.get("dest") == dest:
                return action.get("help") or ""
        raise AssertionError(f"{verb} has no --{dest}")

    @pytest.mark.parametrize(
        "verb",
        ["recent", "milestone-audit", "usage", "anchor-resolve", "anchor-recapture"],
    )
    def test_the_cli_help_names_the_refusal(self, verb):
        assert "negative is an error" in self._cli_help(verb, "limit"), (
            f"`{verb} --limit` now refuses a negative value and its help text does "
            f"not say so -- the divergence has moved from behaviour into docs."
        )

    @pytest.mark.parametrize(
        "tool",
        ["recent", "milestone_audit_query", "claims_list", "anchor_resolve", "anchor_recapture"],
    )
    def test_the_mcp_description_names_the_refusal(self, tool):
        from tests._mcp_schema import collect_tool_schemas

        entry = next((t for t in collect_tool_schemas() if t["name"] == tool), None)
        assert entry is not None, f"{tool} is not on the MCP surface"
        assert "negative" in entry["description"], (
            f"MCP tool `{tool}` now refuses a negative limit and its description "
            f"does not say so."
        )
