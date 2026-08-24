"""The anchor is visible on the ORDINARY read paths (BT-7 Т-56).

Four things are pinned here, and only the first is ordinary coverage.

1. The owner's acceptance, literally: a card whose code has MOVED reports its
   new path on a plain ``get`` with no arguments at all.
2. THE COST, in both directions. A test that only asserts "zero git calls on an
   unanchored page" is green against a build that resolves NOTHING — that is,
   against a unit that did not do its job — so every cost assertion here comes
   in a pair: zero where nothing should be spent, strictly more than zero where
   something must be. The counter sits on ``loc._git``, the module's single git
   seam, AND on ``loc.worktree_root``, because a root lookup is a git call too
   and counting only the first would let the fixed cost hide.
3. THREE populations, not two. A row with no ``meta.loc`` and a row carrying a
   persisted REFUSAL object both cost zero, for different reasons: the first has
   no anchor, the second has one with nothing in it to resolve. On this tracker
   the refusal is the MAJORITY of the anchored population (136 of 158 rows), so
   sending it to git would be the common case, not the rare one.
4. The seam's own failure modes: an extension that raises must not take down the
   read it was decorating, and its failure must be VISIBLE rather than
   indistinguishable from "this card has no anchor" — which is the exact
   conflation the whole design exists to end.

The MCP tests go through the real ``MCPServer``/``call_tool`` pipeline (the
harness ``tests/test_cb107_mcp_surface.py`` uses) rather than a stub, because
what they pin is that the wrapper FORWARDS its flag: a body that hardcoded the
value would satisfy every stub-level signature check and still ignore the
caller (CB-157, measured on a sibling unit the same day).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3 as dbapi
import subprocess
from contextlib import contextmanager

import pytest
from mcp.server.mcpserver import MCPServer

from codebugs import db, findings, loc


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _body(prefix="ALPHA", n=12):
    return "".join(f"{prefix}_VALUE_{i:02d} = compute_the_thing({i})\n" for i in range(1, n + 1))


def _rev(root):
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True
    )
    return out.stdout.decode().strip()


@pytest.fixture()
def tracker(tmp_path):
    """A real repository whose tracker lives inside it, as an ordinary clone's does.

    File-based on purpose: the default root comes from the CONNECTION
    (``db.connection_root``), and an in-memory database has no path, so a
    ``:memory:`` fixture would silently test the no-root branch instead.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text(_body())
    (root / "other.py").write_text(_body("BETA"))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    db.init_project(str(root))
    conn = db.connect(str(root))
    yield root, conn
    conn.close()


def _file(conn, root, *, file="f.py", meta, description, commit=None):
    """One observation through the real add path. Returns the finding id."""
    return findings.add_finding(
        conn,
        severity="low",
        category="anchor_read",
        file=file,
        description=description,
        meta=meta,
        reported_at_commit=commit if commit is not None else _rev(root),
        project_dir=str(root),
        new_category=True,
    )["id"]


_SERIAL = itertools.count()


def _anchored(conn, root, *, n=1, file="f.py"):
    """`n` findings that really carry coordinates, verified rather than assumed.

    Descriptions are serialised per PROCESS, not per call: findings are an
    upsert (CB-43), so two helper calls writing the same text would silently
    return one row and a "page of three" would be a page of one.
    """
    ids = []
    for i in range(n):
        fid = _file(
            conn,
            root,
            file=file,
            meta={"line": i + 1},
            description=f"observation {next(_SERIAL)} about a line that really exists here",
        )
        ids.append(fid)
    for fid in ids:
        card = findings.get_finding(conn, fid, resolve_anchors=False)
        assert card["anchor"]["state"] == "anchored", card["anchor"]
    return ids


def _unanchored(conn, root, *, n=1):
    """`n` findings whose observation names no site at all — no `meta.loc` coordinates.

    Capture PERSISTS a refusal for these (Р7), so they are `refused`, not
    `absent`; the absent population is built by deleting the key outright.
    """
    return [
        _file(
            conn,
            root,
            meta={"note": "nothing to anchor to"},
            description=f"a description with no site grammar in it at all, no {next(_SERIAL)}",
        )
        for _ in range(n)
    ]


def _strip_key(conn, fid):
    """Remove `meta.loc` entirely — the pre-BT-7 population, 79-96% of a tracker."""
    row = conn.execute("SELECT meta FROM findings WHERE id = ?", (fid,)).fetchone()
    meta = json.loads(row[0] or "{}")
    meta.pop("loc", None)
    conn.execute("UPDATE findings SET meta = ? WHERE id = ?", (json.dumps(meta), fid))
    conn.commit()


def _tombstone(conn, fid):
    """`loc: null` — someone said "do not anchor this", which is not "nobody tried"."""
    row = conn.execute("SELECT meta FROM findings WHERE id = ?", (fid,)).fetchone()
    meta = json.loads(row[0] or "{}")
    meta["loc"] = None
    conn.execute("UPDATE findings SET meta = ? WHERE id = ?", (json.dumps(meta), fid))
    conn.commit()


class _GitCounter:
    """Every process this module can spawn, counted by argv.

    Two seams, not one. ``loc._git`` is the module's only ``subprocess.run``, but
    ``worktree_root`` reaches git through ``provenance``, and a counter blind to
    it would report "zero" for a page that had already paid the fixed cost.
    """

    def __init__(self):
        self.calls: list[list[str]] = []

    def install(self, monkeypatch):
        real_git = loc._git
        real_root = loc.worktree_root

        def spy_git(root, args, budget):
            self.calls.append(list(args))
            return real_git(root, args, budget)

        def spy_root(*, project_dir=None):
            self.calls.append(["<worktree_root>"])
            return real_root(project_dir=project_dir)

        # `repo_identity` memoises per root, so a warm cache would make the
        # count depend on test ORDER rather than on the code under test.
        monkeypatch.setattr(loc, "_repo_ids", {})
        monkeypatch.setattr(loc, "_git", spy_git)
        monkeypatch.setattr(loc, "worktree_root", spy_root)
        return self

    @property
    def n(self) -> int:
        return len(self.calls)

    def count(self, *argv: str) -> int:
        """Calls whose argv STARTS with `argv`.

        Prefix-matching rather than first-token matching, because `rev-parse` is
        both the fixed per-TREE call (`--verify HEAD^{commit}`) and a per-ROW one
        (expanding the anchor's own commit); counting the verb would conflate the
        cost this test exists to separate.
        """
        return sum(1 for c in self.calls if list(c[: len(argv)]) == list(argv))

    def containing(self, token: str) -> int:
        """Calls carrying `token` ANYWHERE in argv.

        Needed because `blame` is invoked as `-c core.quotePath=false blame …`,
        so a prefix test for it can never match and would be a pin that is green
        against every possible implementation.
        """
        return sum(1 for c in self.calls if token in c)


@pytest.fixture()
def counter(monkeypatch):
    return _GitCounter().install(monkeypatch)


# --- 1. the owner's acceptance, literally ---------------------------------------------


class TestAnOrdinaryGetSaysWhereTheCodeWent:
    def test_a_card_whose_file_moved_reports_the_new_path_with_no_arguments(self, tracker):
        """The unit's whole reason to exist, asserted through the plain call.

        No `project_dir`, no flag, no second tool call: the shape a human or an
        agent actually types. The root comes from the connection, which is why
        this fixture puts the tracker inside the repository.
        """
        root, conn = tracker
        fid = _file(
            conn,
            root,
            meta={"line": 4},
            description="line four of f.py is wrong, described at length to avoid dedup",
        )
        _git(root, "mv", "f.py", "moved.py")
        _git(root, "commit", "-qm", "move it")

        card = findings.get_finding(conn, fid)
        anchor = card["anchor"]
        assert anchor["state"] == "anchored"
        assert anchor["resolved"] is True
        assert anchor["loc_status"] == "moved_file"
        assert anchor["moved_file"] is True
        assert anchor["path"] == "moved.py"
        assert anchor["stored_path"] == "f.py"
        # The full record travels beside the hoisted three, so a reader can see
        # HOW the answer was reached and not only what it was.
        assert anchor["resolution"]["channel"] == "git"
        assert anchor["resolution"]["survived"] == "1/1"

    def test_a_card_whose_code_did_not_move_says_so_rather_than_saying_nothing(self, tracker):
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        anchor = findings.get_finding(conn, fid)["anchor"]
        assert anchor["loc_status"] == "current"
        assert anchor["moved_file"] is False
        assert anchor["path"] == "f.py"


# --- 2. the cost, in BOTH directions --------------------------------------------------


class TestTheCostIsStructural:
    """§4: a row without coordinates does not REACH the resolver.

    Every assertion is paired. Zero alone is satisfied by a build that resolves
    nothing at all, which is precisely the failed unit this pin has to be able
    to fail against.
    """

    def test_a_card_with_no_anchor_key_spawns_no_process_at_all(self, tracker, counter):
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        _strip_key(conn, fid)
        counter.calls.clear()

        card = findings.get_finding(conn, fid)
        assert counter.n == 0, counter.calls
        assert card["anchor"]["state"] == "absent"
        assert card["anchor"]["resolved"] is False

    def test_but_an_anchored_card_really_does_spend_git(self, tracker, counter):
        """The other half. Without it the assertion above is vacuous."""
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        counter.calls.clear()

        card = findings.get_finding(conn, fid)
        assert counter.n > 0
        assert card["anchor"]["resolved"] is True

    def test_a_persisted_refusal_is_an_anchor_with_nothing_to_resolve(self, tracker, counter):
        """The THIRD population, and the biggest one on a live tracker.

        `loc._stored_loc` calls a refusal object "an anchor is present", which is
        the right answer to its question — a key is there — and the wrong one to
        this seam's. There is no coordinate inside, so a process spawned for it
        would learn nothing.
        """
        root, conn = tracker
        fid = _unanchored(conn, root)[0]
        counter.calls.clear()

        card = findings.get_finding(conn, fid)
        assert counter.n == 0, counter.calls
        assert card["anchor"]["state"] == "refused"
        assert card["anchor"]["reason"] in loc.REASONS
        assert card["anchor"]["resolved"] is False

    def test_a_mixed_page_pays_only_for_the_anchored_rows(self, tracker, counter):
        """The case where the ANSWER is identical and only the price differs.

        A page of one anchored row plus twelve unanchored ones must cost exactly
        what the one anchored row costs alone — so the comparison is against a
        measured baseline rather than against a number written down here, which
        would go stale the first time the cascade changed.
        """
        root, conn = tracker
        alone = _anchored(conn, root)[0]
        counter.calls.clear()
        findings.query_findings(conn, id=alone, resolve_anchors=True)
        baseline = counter.n
        assert baseline > 0

        _unanchored(conn, root, n=6)
        for fid in _unanchored(conn, root, n=6):
            _strip_key(conn, fid)
        counter.calls.clear()
        page = findings.query_findings(conn, resolve_anchors=True, limit=100)
        assert len(page["findings"]) == 13
        assert counter.n == baseline, counter.calls
        states = sorted({f["anchor"]["state"] for f in page["findings"]})
        assert states == ["absent", "anchored", "refused"]


class TestQueryIsCheapByDefault:
    def test_a_page_of_anchored_rows_spawns_nothing_without_the_flag(self, tracker, counter):
        root, conn = tracker
        _anchored(conn, root, n=3)
        counter.calls.clear()

        page = findings.query_findings(conn, limit=100)
        assert counter.n == 0, counter.calls
        # Cheap does NOT mean silent: presence is reported out of the `meta` the
        # row had already read, which is the half that costs nothing.
        assert [f["anchor"]["state"] for f in page["findings"]] == ["anchored"] * 3
        assert all(f["anchor"]["resolved"] is False for f in page["findings"])
        assert all(f["anchor"]["stored_path"] == "f.py" for f in page["findings"])

    def test_the_flag_resolves_the_page_in_one_pass(self, tracker, counter):
        """§3: a batch, never a per-row loop.

        The discriminator is the FIXED cost — the tree context — appearing once
        for a page of three. A per-row implementation returns the same answers
        and pays for it three times, so nothing but a call count can see it.
        """
        root, conn = tracker
        _anchored(conn, root, n=3)
        counter.calls.clear()

        page = findings.query_findings(conn, resolve_anchors=True, limit=100)
        assert all(f["anchor"]["resolved"] for f in page["findings"])
        assert counter.count("<worktree_root>") == 1, counter.calls
        assert counter.count("rev-parse", "--verify", "HEAD^{commit}") == 1, counter.calls


# --- 3. the four answers a reader needs to tell apart ---------------------------------


class TestTheStatesAreNotCollapsed:
    def test_absent_retracted_and_refused_are_three_different_answers(self, tracker):
        """Т-50 closed this conflation on the repair side; the read side must not
        reopen it. `_stored_loc` returns `(False, None)` for a missing key and
        `(True, None)` for the tombstone — identical to a caller that only looks
        at the value, and opposite facts to a human reading the card.
        """
        root, conn = tracker
        absent, retracted = _anchored(conn, root, n=2)
        _strip_key(conn, absent)
        _tombstone(conn, retracted)
        refused = _unanchored(conn, root)[0]
        live = _anchored(conn, root)[0]

        page = findings.query_findings(conn, limit=100)
        by_id = {f["id"]: f["anchor"] for f in page["findings"]}
        assert by_id[absent]["state"] == "absent"
        assert by_id[absent]["reason"] is None
        assert by_id[retracted]["state"] == "retracted"
        assert by_id[retracted]["reason"] == "retracted"
        assert by_id[refused]["state"] == "refused"
        assert by_id[live]["state"] == "anchored"
        assert len({by_id[i]["state"] for i in (absent, retracted, refused, live)}) == 4

    def test_a_broken_stored_object_is_invalid_and_not_refused(self, tracker):
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        row = conn.execute("SELECT meta FROM findings WHERE id = ?", (fid,)).fetchone()
        meta = json.loads(row[0])
        meta["loc"]["line"] = -1
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", (json.dumps(meta), fid))
        conn.commit()
        anchor = findings.get_finding(conn, fid)["anchor"]
        assert anchor["state"] == "invalid"
        assert anchor["reason"] == "invalid_anchor"

    def test_a_meta_column_that_parses_but_is_not_an_object_is_unreadable(self):
        """Review falsified the docstring that used to sit above this branch:
        `"a string"`, `[1, 2]` and `123` all PARSE, so `row_to_dict` does not
        raise and the parsed branch is reached with a non-object. It is a
        different fact from "carries no anchor" and must not read as one.
        """
        assert loc.classify_row({"meta": "a string"})[0] == "unreadable"
        assert loc.classify_row({"meta": [1, 2]})[0] == "unreadable"
        assert loc.classify_row({"meta": 123})[0] == "unreadable"

    def test_the_two_column_shapes_agree_on_a_stored_json_null(self):
        """The drift the reader's own docstring warns about: `meta` holding the
        JSON literal `null` must not be `unreadable` through one branch and
        `absent` through the other."""
        assert loc.classify_row({"meta_json": "null"})[0] == "unreadable"
        assert loc.classify_row({"meta": None})[0] == "unreadable"
        # A row that carries no meta key at all is a different thing again.
        assert loc.classify_row({})[0] == "absent"

    def test_every_state_in_the_closed_vocabulary_is_reachable_from_a_row(self):
        """`unreadable` is reachable only from a RAW row (the batch surface), and
        saying so here is what stops it being quietly dropped from the
        vocabulary as unused.
        """
        assert loc.classify_row({"meta_json": "{not json"})[0] == "unreadable"
        assert loc.classify_row({"meta_json": "{}"})[0] == "absent"
        assert loc.classify_row({"meta": {}})[0] == "absent"
        assert loc.classify_row({"meta": {"loc": None}})[0] == "retracted"
        assert loc.classify_row({"meta": {"loc": {"v": 2, "skipped": "no_grammar"}}})[0] == "refused"
        assert loc.classify_row({"meta": {"loc": {"v": 99}}})[0] == "invalid"
        assert set(loc.SUMMARY_STATES) == {
            "absent",
            "unreadable",
            "invalid",
            "retracted",
            "refused",
            "anchored",
            # Not produced by `classify_row` — it is the SEAM's answer for a row
            # the extension could not answer for, and it belongs in the closed
            # vocabulary precisely so a reader can tell it from a silence.
            "unavailable",
        }

    def test_moved_file_is_none_and_never_false_when_nothing_was_checked(self, tracker):
        """"Did not move" and "was not looked at" are the same two facts this
        vocabulary refuses to merge, one level down."""
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        anchor = findings.get_finding(conn, fid, resolve_anchors=False)["anchor"]
        assert anchor["moved_file"] is None
        assert anchor["loc_status"] is None
        assert anchor["path"] is None


# --- 4. the seam itself ---------------------------------------------------------------


class TestTheSeamFailsVisiblyAndNeverFatally:
    @staticmethod
    def _replace(monkeypatch, fn):
        entry = next(e for e in db._read_enrichers if e.name == "loc.anchor")
        monkeypatch.setattr(
            db,
            "_read_enrichers",
            [db.ReadEnricher(entry.name, fn, entry.key, entry.fallback)],
        )

    def test_an_enricher_that_raises_does_not_take_down_the_read(self, tracker, monkeypatch):
        root, conn = tracker
        fid = _anchored(conn, root)[0]

        def boom(conn, rows, *, resolve, project_dir=None):
            raise RuntimeError("the extension fell over")

        self._replace(monkeypatch, boom)
        card = findings.get_finding(conn, fid)
        assert card["id"] == fid
        assert card["description"]

    def test_and_the_failure_is_VISIBLE_rather_than_an_absent_key(self, tracker, monkeypatch):
        """The decision §2 demanded be made explicitly, pinned as behaviour.

        Swallow-and-log is what the older WRITING seams do, and copying it here
        would have been the defect: an MCP caller never sees stderr, so a
        silently missing key is indistinguishable from "this card carries no
        anchor" — the very conflation the summary exists to end.
        """
        root, conn = tracker
        fid = _anchored(conn, root)[0]

        def boom(conn, rows, *, resolve, project_dir=None):
            raise RuntimeError("the extension fell over")

        self._replace(monkeypatch, boom)
        anchor = findings.get_finding(conn, fid)["anchor"]
        assert anchor["state"] == "unavailable"
        assert "RuntimeError" in anchor["error"]
        # And it is a SUMMARY, not a second narrower object. An earlier draft
        # asserted the opposite — that `unavailable` sat OUTSIDE the closed
        # vocabulary — and review measured what that cost: the two-key dict made
        # every consumer written against the documented ten-key shape raise
        # `KeyError` on exactly the path this seam exists to make survivable.
        assert anchor["state"] in loc.SUMMARY_STATES
        assert set(anchor) == {
            "state", "reason", "stored_path", "resolved", "loc_status",
            "moved_file", "path", "line", "end", "resolution", "error",
        }
        assert anchor["resolved"] is False
        assert anchor["moved_file"] is None

    def test_an_enricher_that_silently_skips_a_row_is_caught_too(self, tracker, monkeypatch):
        """A pass that returns normally without writing the key is the same lie
        arriving quietly, so the runner GUARANTEES the key rather than trusting
        the extension to have written it."""
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        self._replace(monkeypatch, lambda conn, rows, **kw: None)
        anchor = findings.get_finding(conn, fid)["anchor"]
        assert anchor["state"] == "unavailable"
        assert "no summary" in anchor["error"]
        assert anchor["loc_status"] is None

    def test_unregistering_the_extension_removes_the_summary_and_the_read_survives(
        self, tracker, monkeypatch
    ):
        """The seam mutant (§8.7). The key vanishes ENTIRELY — not to `absent`,
        which would be core inventing an answer on behalf of an extension that
        is not there."""
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        monkeypatch.setattr(db, "_read_enrichers", [])
        card = findings.get_finding(conn, fid)
        assert "anchor" not in card
        assert card["id"] == fid

    def test_a_per_tree_git_failure_does_not_rewrite_rows_that_asked_for_nothing(
        self, tracker, monkeypatch
    ):
        """Review's finding, and the worst one it found: the tree context is
        built ONCE for a whole page, so an unguarded failure there escaped into
        the enricher's guard and stamped `unavailable` on EVERY row — including
        rows carrying no anchor, whose correct answer needs no git and therefore
        cannot be wrong. That is this design's own conflation, arriving through
        the failure path.
        """
        import subprocess as sp

        root, conn = tracker
        anchored = _anchored(conn, root)[0]
        bare = _anchored(conn, root)[0]
        _strip_key(conn, bare)

        def explode(root, args, budget):
            raise sp.TimeoutExpired(cmd="git", timeout=2.0)

        monkeypatch.setattr(loc, "_repo_ids", {})
        monkeypatch.setattr(loc, "_git", explode)
        page = findings.query_findings(conn, resolve_anchors=True, limit=100)
        by_id = {f["id"]: f["anchor"] for f in page["findings"]}

        # The row that asked for nothing keeps its real answer.
        assert by_id[bare]["state"] == "absent"
        assert by_id[bare]["reason"] is None
        # The row that did ask gets a degraded RESOLUTION, not a degraded row.
        assert by_id[anchored]["state"] == "anchored"
        assert by_id[anchored]["loc_status"] == "unknown"
        assert by_id[anchored]["reason"] == "timeout"

    def test_an_os_level_failure_degrades_the_same_way(self, tracker, monkeypatch):
        """`subprocess.run` raises `OSError` of its own — a git that is not
        executable, EMFILE, ENOMEM. CB-79's family, and the reason the guard
        catches the tree and not one errno."""
        root, conn = tracker
        fid = _anchored(conn, root)[0]

        def explode(root, args, budget):
            raise PermissionError("git is not executable")

        monkeypatch.setattr(loc, "_repo_ids", {})
        monkeypatch.setattr(loc, "_git", explode)
        anchor = findings.get_finding(conn, fid)["anchor"]
        assert anchor["state"] == "anchored"
        assert anchor["reason"] == "internal_error"

    def test_registering_the_same_name_with_a_different_body_is_refused(self):
        """CB-15's shape at the seam level: a second implementation that never
        runs while its author believes it registered."""
        with pytest.raises(ValueError, match="never run"):
            db.register_read_enricher("loc.anchor", lambda *a, **k: None, key="anchor")

    def test_two_extensions_may_not_claim_one_key(self):
        with pytest.raises(ValueError, match="already declared"):
            db.register_read_enricher("someone.else", lambda *a, **k: None, key="anchor")

    def test_re_registering_the_identical_contract_is_a_no_op(self):
        before = len(db._read_enrichers)
        db.register_read_enricher(
            "loc.anchor",
            loc.enrich_findings,
            key=loc.SUMMARY_KEY,
            fallback=loc.unavailable_summary,
        )
        assert len(db._read_enrichers) == before

    def test_a_changed_fallback_is_a_changed_contract(self):
        """The fallback decides what a reader sees when everything else failed,
        so a silently replaced one is the same CB-15 shape as a replaced body."""
        with pytest.raises(ValueError, match="never run"):
            db.register_read_enricher(
                "loc.anchor",
                loc.enrich_findings,
                key=loc.SUMMARY_KEY,
                fallback=lambda error: {"state": "unavailable", "error": error},
            )

    def test_the_seam_is_read_only_and_declares_it(self):
        """It writes nothing, so it inherits none of the writing seams'
        transaction machinery — and copying that machinery is the failure this
        assertion exists to make loud."""
        source = db.run_read_enrichers.__doc__ or ""
        assert "NEVER RAISES" in source
        import inspect

        body = inspect.getsource(db.run_read_enrichers)
        assert "SAVEPOINT" not in body
        assert "commit" not in body


class TestTheRootIsNotAmbientCwd:
    def test_the_default_root_comes_from_the_connection(self, tracker):
        root, conn = tracker
        assert db.connection_root(conn) == str(root)

    def test_a_pathless_database_has_no_root_and_the_read_says_so(self, tmp_path):
        """BT-7 Р3 refuses ambient cwd, so "no root" must be reported and never
        guessed at. An in-memory tracker is the reachable instance."""
        c = dbapi.connect(":memory:")
        c.row_factory = dbapi.Row
        findings.ensure_schema(c)
        assert db.connection_root(c) is None
        fid = findings.add_finding(
            c,
            severity="low",
            category="anchor_read",
            file="f.py",
            description="an observation in a tracker that has no directory of its own",
            new_category=True,
            finding_id="CB-EXPLICIT-1",
        )["id"]
        # `meta.loc` is reserved on ADD (it is the resolver's output, never a
        # caller's input); `update` is the sanctioned way in, and is exactly why
        # the key is declared UPDATABLE.
        findings.update_finding(
            c,
            fid,
            meta_update={
                "loc": {
                    "v": 2, "repo": "a" * 40, "commit": "b" * 40, "path": "f.py",
                    "line": 1, "end": 1, "text": ["x = 1"], "norm": "v1",
                    "sites_dropped": 0,
                }
            },
        )
        anchor = findings.get_finding(c, fid)["anchor"]
        assert anchor["state"] == "anchored"
        assert anchor["loc_status"] == "unknown"
        assert anchor["reason"] == "no_root"
        c.close()


# --- 5. the surfaces actually forward the flag (CB-157) -------------------------------


def _mcp(root):
    """A real server over a connection the SDK's worker thread may touch.

    `call_tool` runs the handler off the calling thread, and a sqlite connection
    is bound to the thread that made it — so the fixture's own connection cannot
    be reused here. Opening a second one against the same FILE also exercises
    the default-root path honestly: `db.connection_root` reads the root off THIS
    connection, not off the fixture's.
    """
    path = str(root / ".codebugs" / "findings.db")

    @contextmanager
    def factory():
        conn = dbapi.connect(path, check_same_thread=False)
        conn.row_factory = dbapi.Row
        try:
            yield conn
        finally:
            conn.close()

    server = MCPServer("t")
    findings.register_tools(server, factory)
    return server


def _call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


class TestTheMcpWrappersForwardTheFlag:
    """CB-157: a wrapper whose body HARDCODES the value passes every signature
    check and ignores its caller. Both directions are asserted, because a mutant
    that pins the flag to either constant must turn one of them red.
    """

    def test_get_resolves_by_default_and_the_opt_out_reaches_the_domain(
        self, tracker, counter
    ):
        root, conn = tracker
        fid = _anchored(conn, root)[0]
        server = _mcp(root)

        counter.calls.clear()
        _call(server, "get", finding_id=fid)
        greedy = counter.n
        assert greedy > 0

        counter.calls.clear()
        _call(server, "get", finding_id=fid, resolve_anchors=False)
        assert counter.n == 0, counter.calls

    def test_query_is_cheap_by_default_and_the_flag_reaches_the_domain(
        self, tracker, counter
    ):
        root, conn = tracker
        _anchored(conn, root, n=2)
        server = _mcp(root)

        counter.calls.clear()
        _call(server, "query", limit=100)
        assert counter.n == 0, counter.calls

        counter.calls.clear()
        _call(server, "query", limit=100, resolve_anchors=True)
        assert counter.n > 0

    def test_the_response_carries_the_summary_over_the_real_wire(self, tracker):
        """The gate on the RESPONSE shape is behavioural, not the golden: no
        `outputSchema` is snapshotted and the live schema carries
        `additionalProperties: True`, so a golden-based gate here could never
        fire. Same reasoning as BT-5's `attention` block.
        """
        root, conn = tracker
        fid = _file(
            conn,
            root,
            meta={"line": 4},
            description="a card whose code is about to move somewhere else entirely",
        )
        _git(root, "mv", "f.py", "elsewhere.py")
        _git(root, "commit", "-qm", "move")
        server = _mcp(root)

        result = _call(server, "get", finding_id=fid)
        # Unwrapped exactly as BT-5's `attention` gate does
        # (`tests/test_server.py`): the summary must survive SERIALISATION, not
        # merely exist in the Python return value.
        anchor = json.loads(result.content[0].text)["anchor"]
        assert anchor["loc_status"] == "moved_file"
        assert anchor["path"] == "elsewhere.py"
        assert set(anchor) == {
            "state",
            "reason",
            "stored_path",
            "resolved",
            "loc_status",
            "moved_file",
            "path",
            "line",
            "end",
            "resolution",
        }


class TestTheCliVerbsForwardTheFlag:
    def test_get_prints_the_resolution_and_the_opt_out_skips_the_work(
        self, tracker, counter, capsys, monkeypatch
    ):
        import sys

        from codebugs import cli

        root, conn = tracker
        fid = _file(
            conn,
            root,
            meta={"line": 4},
            description="a card read from the command line after its file moved away",
        )
        _git(root, "mv", "f.py", "gone.py")
        _git(root, "commit", "-qm", "move")
        monkeypatch.setenv("CODEBUGS_ROOT", str(root))
        counter.calls.clear()

        monkeypatch.setattr(sys, "argv", ["codebugs", "get", fid])
        cli.main()
        printed = json.loads(capsys.readouterr().out)
        assert printed["anchor"]["path"] == "gone.py"
        assert counter.n > 0

        counter.calls.clear()
        monkeypatch.setattr(sys, "argv", ["codebugs", "get", fid, "--no-resolve-anchor"])
        cli.main()
        printed = json.loads(capsys.readouterr().out)
        assert printed["anchor"]["state"] == "anchored"
        assert printed["anchor"]["resolved"] is False
        assert counter.n == 0, counter.calls

    def test_query_grows_a_loc_column_only_under_the_flag(
        self, tracker, counter, capsys, monkeypatch
    ):
        import sys

        from codebugs import cli

        root, conn = tracker
        _anchored(conn, root, n=2)
        monkeypatch.setenv("CODEBUGS_ROOT", str(root))

        counter.calls.clear()
        monkeypatch.setattr(sys, "argv", ["codebugs", "query"])
        cli.main()
        plain = capsys.readouterr().out
        assert counter.n == 0, counter.calls
        assert "loc" not in plain.split("\n")[0]

        counter.calls.clear()
        monkeypatch.setattr(sys, "argv", ["codebugs", "query", "--resolve-anchors"])
        cli.main()
        resolved = capsys.readouterr().out
        assert counter.n > 0
        assert "loc" in resolved.split("\n")[0]
        assert "current" in resolved


# --- 6. staleness_check, the conditional half of the unit ------------------------------


class TestStalenessCarriesTheAnchorToo:
    """§5's condition was MET, so the path was built rather than deferred.

    `check_findings` already reads its rows through `findings.query_findings`,
    so the summary arrives through the same seam and no new inter-module
    dependency was introduced — which mattered, because `loc` imports
    `provenance` and the reverse import would be a cycle.
    """

    def test_a_record_says_what_became_of_the_file_and_where_the_lines_went(self, tracker):
        from codebugs import provenance

        root, conn = tracker
        fid = _file(
            conn,
            root,
            meta={"line": 4},
            description="a staleness question about a card whose file is about to move",
        )
        _git(root, "mv", "f.py", "renamed.py")
        _git(root, "commit", "-qm", "move")

        report = provenance.check_findings(conn, str(root), resolve_anchors=True)
        record = next(r for r in report["findings"] if r["finding_id"] == fid)
        # The two answer NEIGHBOURING questions, which is the reason to carry both.
        assert record["file_status"] in ("renamed", "deleted", "modified")
        assert record["anchor"]["loc_status"] == "moved_file"
        assert record["anchor"]["path"] == "renamed.py"

    def test_the_cheap_half_is_there_without_the_flag(self, tracker, counter):
        from codebugs import provenance

        root, conn = tracker
        _anchored(conn, root, n=2)
        counter.calls.clear()
        report = provenance.check_findings(conn, str(root))
        # `file_status` spends git of its own, so this is NOT a zero-cost claim;
        # what it pins is that no ANCHOR resolution happened.
        assert counter.containing("blame") == 0, counter.calls
        # The counter's own sanity: a resolving run DOES reach blame, so the
        # zero above is a fact about the flag and not about the matcher.
        counter.calls.clear()
        provenance.check_findings(conn, str(root), resolve_anchors=True)
        assert counter.containing("blame") > 0, counter.calls
        assert all(r["anchor"]["state"] == "anchored" for r in report["findings"])
        assert all(r["anchor"]["resolved"] is False for r in report["findings"])


class TestTheGreedyDefaultDoesNotLeakIntoInternalCallers:
    """`get_finding` resolves by DEFAULT, which is right for a caller that will
    SHOW the card and wrong for every internal probe that discards the row.

    Both instances are in `provenance`: the existence check inside
    `check_findings`, and `resolve_trailers`, which touches one card per trailer
    over a whole rev-range. Neither looks at the summary, so a greedy read there
    would be two to four subprocesses spent on nothing — a cost regression on a
    path this unit was never asked to touch.
    """

    def test_resolve_trailers_spends_no_git_on_anchors(self, tracker, counter):
        from codebugs import provenance

        root, conn = tracker
        fid = _anchored(conn, root)[0]
        (root / "f.py").write_text(_body("GAMMA"))
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"fix it\n\nResolves: {fid}")
        counter.calls.clear()

        report = provenance.resolve_trailers(conn, rev_range="HEAD~1..HEAD", project_dir=str(root))
        assert fid in report["resolved"]
        assert counter.containing("blame") == 0, counter.calls

    def test_the_existence_probe_inside_staleness_spends_none_either(self, tracker, counter):
        from codebugs import provenance

        root, conn = tracker
        fid = _anchored(conn, root)[0]
        counter.calls.clear()
        provenance.check_findings(conn, str(root), finding_id=fid)
        assert counter.containing("blame") == 0, counter.calls


class TestThereIsExactlyOneResolver:
    """§6's boundary, which nothing else in the suite holds.

    The read path calls the SAME machinery `anchor_resolve` does. An inline
    "cheap" resolver beside the real one is two spellings of one decision, and
    the two diverge the first time either learns something — this repository's
    most-repeated defect. A behavioural test cannot see it (a faithful copy
    returns the same answers), so the pin is structural.
    """

    @staticmethod
    def _calls(fn) -> set[str]:
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    def test_both_entry_points_go_through_the_same_two_helpers(self):
        for fn in (loc.resolve_findings, loc.summarize_rows):
            called = self._calls(fn)
            assert "_resolve_one" in called, fn.__name__
            assert "_resolution_context" in called, fn.__name__

    def test_and_neither_reaches_git_on_its_own(self):
        """`_git` and `resolve_anchor` are reached THROUGH `_resolve_one`, never
        beside it — a second call site is how the copy starts."""
        assert "_git" not in self._calls(loc.summarize_rows)
        assert "resolve_anchor" not in self._calls(loc.summarize_rows)
        assert "_git" not in self._calls(loc.resolve_findings)
        assert "resolve_anchor" not in self._calls(loc.resolve_findings)

    def test_findings_never_names_the_resolver_itself(self):
        """The domain module hands rows to the SEAM and learns nothing about how
        an extension resolves them — the reason the seam exists at all."""
        import pathlib

        source = pathlib.Path(findings.__file__).read_text()
        assert "import loc" not in source
        assert "from codebugs.loc" not in source
        assert "resolve_anchor(" not in source
