"""CB-230: a SERVICE write must not move `updated_at`.

`updated_at` answers *when did somebody last change this card*. The mass anchor
capture of 2026-08-24 wrote its machine-derived `meta.loc` through the shared
`findings.update_finding`, which stamped the column unconditionally, and so
overwrote the last-change date of 136 of this tracker's 233 cards (3285 of
autosorter's 3449) inside one six-second window. The dates are gone; nothing
here restores them. What this file pins is that the NEXT capture cannot repeat
it, and that the flag which makes that possible cannot be used for anything
else.

Six of the seven properties below are held by a mutant that turns a named test
red. The seventh is stated as such in its own docstring: it pins behaviour the
change deliberately PRESERVED, and one of the two mutants written against it is
measured to survive — for a reason that is a property of the design rather than
a gap in the test. Saying which is which is the point (CLAUDE.md: "a test that
passes on both sides can still be right, but only when it pins behaviour the
change deliberately preserved; say so in its name or docstring").
"""

from __future__ import annotations

import argparse
import ast
import inspect
import pathlib
import sqlite3
import subprocess

import pytest

from codebugs import db, findings, loc

# A timestamp no real clock in this suite can produce, so an assertion that the
# column DID move is a positive statement about this call rather than about how
# long the test took. Whole-second `utc_now` is exactly why real time cannot be
# used here: two calls inside one second are indistinguishable by clock, which is
# the same reason `claims` discriminates on `touch_count` instead.
LATER = "2099-01-02T03:04:05Z"


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add(conn, *, desc="the failure text", cat="bug", file="a.py", severity="high", **kw):
    return findings.add_finding(
        conn,
        severity=severity,
        category=cat,
        file=file,
        description=desc,
        new_category=True,
        **kw,
    )


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _body(prefix="ALPHA", n=12):
    """Twelve lines, each long enough to clear the capture's minimum span."""
    return "".join(f"{prefix}_VALUE_{i:02d} = compute_the_thing({i})\n" for i in range(1, n + 1))


@pytest.fixture
def anchored_card(tmp_path):
    """A real repository, a real card, and a stored anchor that needs rebuilding.

    Shaped after `tests/test_loc.py::TestRecaptureFourPoints._tracked`, which is
    the fixture the anchor verb's own tests use — the same repository layout, the
    same `meta={"line": "4-6"}` grammar and the same `new_category=True`. The
    stored anchor is then deliberately replaced with a REFUSAL object so that the
    recapture has something real to rewrite: a run that changes nothing would
    leave `updated_at` alone for the wrong reason, and the assertion would prove
    nothing.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    findings.ensure_schema(conn)

    root = tmp_path / "r"
    root.mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "mod.py").write_text(_body())
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    first = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True
    ).stdout.decode().strip()

    finding_id = findings.add_finding(
        conn,
        severity="low",
        category="anchor_recap",
        file="mod.py",
        description="a card whose anchor the repair verb will rebuild (CB-230)",
        meta={"line": "4-6"},
        reported_at_commit=first,
        new_category=True,
    )["id"]
    findings.update_finding(
        conn,
        finding_id,
        meta_update={"loc": {"v": 2, "skipped": "timeout", "sites_dropped": 0}},
    )
    before = conn.execute(
        "SELECT updated_at FROM findings WHERE id = ?", (finding_id,)
    ).fetchone()["updated_at"]

    yield conn, root, finding_id, before, first
    conn.close()


def _stored_updated_at(conn, finding_id: str) -> str:
    row = conn.execute("SELECT updated_at FROM findings WHERE id = ?", (finding_id,)).fetchone()
    assert row is not None, f"{finding_id} vanished"
    return row["updated_at"]


class TestServiceWriteLeavesTheColumnAlone:
    """Oracle 1 and 2: the flag decides the column, and its DEFAULT is authored."""

    def test_a_service_write_changes_meta_and_not_updated_at(self, conn, monkeypatch):
        """Oracle 1. Mutant: drop the `if authored:` guard — this goes red.

        The clock is moved rather than waited on, so the assertion is about this
        call and not about the wall time the suite happened to take.
        """
        f = _add(conn)
        before = _stored_updated_at(conn, f["id"])

        monkeypatch.setattr(findings, "utc_now", lambda: LATER)
        out = findings.update_finding(
            conn, f["id"], meta_update={"loc": {"line": 7}}, authored=False
        )

        assert out["meta"]["loc"] == {"line": 7}, "the service payload did not land"
        assert _stored_updated_at(conn, f["id"]) == before, (
            "a service write moved updated_at — CB-230 verbatim"
        )
        assert out["updated_at"] == before, "the RETURNED row disagrees with the stored one"

    def test_an_authored_write_still_moves_updated_at(self, conn, monkeypatch):
        """Oracle 2. Mutant: make `authored=False` the default — this goes red.

        Without this test the default is unpinned, and flipping it would silently
        stop every ordinary edit from recording that it happened.
        """
        f = _add(conn)
        before = _stored_updated_at(conn, f["id"])
        assert before != LATER

        monkeypatch.setattr(findings, "utc_now", lambda: LATER)
        out = findings.update_finding(conn, f["id"], meta_update={"note_ish": 1})

        assert _stored_updated_at(conn, f["id"]) == LATER, (
            "an authored write did not stamp updated_at"
        )
        assert out["updated_at"] == LATER

    def test_the_no_op_path_is_unaffected_by_the_flag(self, conn, monkeypatch):
        """A call that writes nothing stamps nothing, with or without the flag.

        Pins the ordering of the two guards: `if not updates` is decided BEFORE
        the flag is consulted, so a service call carrying no payload cannot
        acquire a different no-op behaviour from an authored one.
        """
        f = _add(conn)
        before = _stored_updated_at(conn, f["id"])
        monkeypatch.setattr(findings, "utc_now", lambda: LATER)

        findings.update_finding(conn, f["id"])
        assert _stored_updated_at(conn, f["id"]) == before
        findings.update_finding(conn, f["id"], authored=False)
        assert _stored_updated_at(conn, f["id"]) == before


class TestStatusUnderTheFlagIsRefused:
    """Oracle 4: a status change is authorship by definition."""

    def test_status_with_authored_false_raises_and_writes_nothing(self, conn, monkeypatch):
        """Mutant: delete the `raise` — this goes red.

        Both halves matter. The refusal is a `ValueError` (the domain contract),
        and it lands NOTHING: it is raised from the argument-validation block
        above `db.txn`, so it never takes the write lock. A refusal that had
        already written the status would be the CB-15/CB-16 lie.
        """
        f = _add(conn)
        before = _stored_updated_at(conn, f["id"])
        monkeypatch.setattr(findings, "utc_now", lambda: LATER)

        with pytest.raises(ValueError, match="authored=False"):
            findings.update_finding(conn, f["id"], status="fixed", authored=False)

        row = findings.get_finding(conn, f["id"])
        assert row["status"] == "open", "the refused status write landed anyway"
        assert _stored_updated_at(conn, f["id"]) == before

    def test_the_refusal_survives_a_status_alias(self, conn):
        """The guard sits AFTER `resolve_finding_status`, so an alias cannot slip past.

        `status` is normalized before the check, which means the refusal keys on
        "a status was requested", never on its spelling — the CB-19 lesson applied
        to a gate rather than to a filter.
        """
        f = _add(conn)
        with pytest.raises(ValueError, match="authored=False"):
            findings.update_finding(conn, f["id"], status="FIXED", authored=False)

    def test_an_invalid_status_is_still_reported_as_an_invalid_status(self, conn):
        """Order pin: the vocabulary resolver runs first, so its message wins.

        A caller passing both a nonsense status and the flag is told the status is
        nonsense — the more specific complaint — rather than being sent to argue
        about authorship over a value that was never going to be accepted.
        """
        f = _add(conn)
        with pytest.raises(ValueError) as excinfo:
            findings.update_finding(conn, f["id"], status="not-a-status", authored=False)
        assert "authored=False" not in str(excinfo.value)


class TestStatusHooksAreNotCollateralDamage:
    """Oracle 5. THIS CLASS PINS PRESERVED BEHAVIOUR, and one named mutant SURVIVES.

    The edit cuts a new `if` into the block that also fires
    `db.run_status_change_hooks`, so the risk being covered is real: an edit that
    swallowed the hook call into the new branch would break status projection
    silently. Two mutants were written and both were RUN.

    * Relocating the hook statements INTO the `if authored:` block turns this
      class red — that block sits above `conn.execute`, so `cur` is unbound and
      every authored status write raises.
    * Widening the hook's own guard to `if authored and status is not None and
      ...` SURVIVES, and the reason is a property of the design rather than a hole
      in the test: `authored=False` together with `status=` is REFUSED
      (TestStatusUnderTheFlagIsRefused), so `status is not None` already implies
      `authored`. The two conditions are not independent and no behaviour can
      separate them. Recorded rather than papered over.
    """

    def test_an_authored_status_write_still_fires_the_status_change_hooks(self, conn, monkeypatch):
        """The observer is `db.run_status_change_hooks` itself, not a registered hook.

        That is this suite's existing convention (`tests/test_claims.py:538`,
        `tests/test_findings.py:719`) and it is the right one here: the registry
        is a process global, so registering a probe would outlive the test, while
        patching the call site observes exactly the statement this edit sits next
        to.
        """
        fired: list[tuple] = []
        monkeypatch.setattr(db, "run_status_change_hooks", lambda *a, **k: fired.append(a[1:]))

        f = _add(conn)
        findings.update_finding(conn, f["id"], status="fixed")

        assert fired == [(f["id"], "open", "fixed")], (
            "the status-change hook did not fire on an ordinary authored close"
        )

    def test_a_service_write_fires_no_status_hook_because_it_cannot_carry_a_status(
        self, conn, monkeypatch
    ):
        """The other half, and it is a consequence rather than a second rule.

        A service write can never fire a status hook, because the only argument
        that fires one is refused under the flag. Pinned so that a future
        relaxation of the refusal has to confront this line.
        """
        fired: list[tuple] = []
        monkeypatch.setattr(db, "run_status_change_hooks", lambda *a, **k: fired.append(a[1:]))

        f = _add(conn)
        findings.update_finding(conn, f["id"], meta_update={"loc": None}, authored=False)

        assert fired == []


class TestServiceWriteCallSiteRatchet:
    """Oracle 6 and 7: exactly one service caller, and no surface can become one.

    Modelled on `tests/test_dedup.py::TestEscalateOptOutRatchet`, including its
    second test, and read by AST rather than by grep for that class's own reason:
    `tests/test_fsio.py::TestWriteCallSitesRatchet` was first written as a text
    search and matched its own docstrings. A stated count in this repository is
    held by a test, because the two places one was left as prose it was wrong
    ("three copies" was four; CB-24's four sites were nineteen).

    The scope is the whole PACKAGE, not one module: the single service caller
    lives in `loc.py` while the parameter lives in `findings.py`, so a
    single-file reader would count zero and pass forever.
    """

    def _opt_out_sites(self) -> list[tuple[str, int]]:
        package = pathlib.Path(findings.__file__).parent
        sites: list[tuple[str, int]] = []
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg == "authored"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        sites.append((path.name, node.lineno))
        return sites

    def test_exactly_one_call_site_declines_authorship(self):
        """Mutant: add a second `authored=False` anywhere in the package — red."""
        sites = self._opt_out_sites()
        assert len(sites) == 1, (
            f"expected exactly one `authored=False` call site "
            f"(loc.recapture_findings' anchor CAS), found {len(sites)}: {sites}"
        )
        assert sites[0][0] == "loc.py", f"the one service caller moved: {sites}"

    def test_the_public_surfaces_cannot_decline_authorship(self):
        """Oracle 7. Mutant: forward the flag to the MCP wrapper — red.

        The parameter is in-package only. An MCP client or an operator declining
        to author would be exactly the ability CB-230 exists to withhold: a caller
        who can suppress the record of their own edit. The readers are the parity
        gate's, deliberately — one technique, so the two files cannot disagree
        about what "the surface" is.
        """
        assert "authored" in inspect.signature(findings.update_finding).parameters, (
            "the domain parameter itself is gone — this ratchet now guards nothing"
        )

        captured: dict[str, object] = {}

        class _Mcp:
            def tool(self, *a, **kw):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn

                return deco

        findings.register_tools(_Mcp(), lambda: None)
        assert "update" in captured, "the MCP `update` tool is not registered"
        assert "authored" not in inspect.signature(captured["update"]).parameters, (
            "`authored` leaked onto the MCP surface"
        )

        parsers: dict[str, argparse.ArgumentParser] = {}

        class _Sub:
            def add_parser(self, name, **kw):
                p = argparse.ArgumentParser(add_help=False)
                parsers[name] = p
                return p

        findings.register_cli(_Sub(), {})
        assert "update" in parsers, "the CLI `update` verb is not registered"
        dests = {a.dest for a in parsers["update"]._actions}
        assert "authored" not in dests, "`authored` leaked onto the CLI surface"


class TestAnchorRecaptureIsAServiceWrite:
    """Oracle 3: the test that reproduces the INCIDENT, not a property of a function.

    Everything above proves things about `update_finding`. This proves the fix:
    the real `loc.recapture_findings`, over a real repository, rewriting a real
    anchor, leaves `updated_at` where it was. Mutant: drop `authored=False` from
    the call in `loc.py` — this goes red while every test above stays green,
    which is exactly why it exists.
    """

    def test_recapture_rewrites_the_anchor_without_moving_updated_at(
        self, anchored_card, monkeypatch
    ):
        conn, root, finding_id, before, first = anchored_card
        monkeypatch.setattr(findings, "utc_now", lambda: LATER)

        out = loc.recapture_findings(
            conn, finding_id=finding_id, project_dir=str(root), apply=True
        )

        # The rewrite must actually have happened, or the assertion below is
        # vacuous: a run that touched nothing also leaves `updated_at` alone.
        assert out["results"][0]["outcome"] == "updated", out
        stored = findings.get_finding(conn, finding_id)["meta"]["loc"]
        assert stored == loc.capture(
            {
                "file": "mod.py",
                "meta": {"line": "4-6"},
                "reported_at_commit": first,
                "project_dir": str(root),
            }
        ), "the repair did not produce the anchor a file-time capture would have"

        assert _stored_updated_at(conn, finding_id) == before, (
            "the anchor capture moved updated_at — the 2026-08-24 incident, again"
        )
