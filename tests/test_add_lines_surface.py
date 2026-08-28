"""CB-233 — the code location gets a NAMED input on the MCP surface, under the
spelling the CLI and the grammar already use.

WHAT THIS IS NOT. It is not a new mechanism. `meta.lines` has reached the anchor
since BT-7, the grammar accepts every spelling the corpus writes, and the CLI has
had `-l/--lines` since CB-129. The hole was one of SURFACE PARITY, and it was
one-sided: the tracker's cards are filed by agents through MCP, and the MCP `add`
tool carried no named input for a location at all — only a free-text `file` and an
untyped `meta` whose own description listed `lines` as a throwaway example beside
`module` and `rule_code`. A filer reading that had no reason to pass it.

WHAT THE TESTS HERE HOLD, in the order the unit's oracle names them:

1. the parameter reaches the anchor THROUGH THE REAL MCP TOOL, not through the
   domain function underneath it;
2. it does so for the tokens people ACTUALLY WRITE — 26 of them, harvested from
   the descriptions of real cards in this repository's own tracker, with their
   own `file` columns, and split 12/14 between paths that name the card's own
   file and paths that name another one;
3. all four spellings the grammar accepts survive the new parameter;
4. ONE rule governs the two-spellings conflict, on BOTH surfaces — the test that
   would go red if the CLI and MCP ever answered the same input differently;
5. a bare line number works against the `file` column, which is what makes the
   input cheap: 51 of this tracker's 51 live cards carry a real path there;
6. `None` is "not supplied" and an explicitly supplied empty value is a refusal,
   never a silent nothing (CB-133's rule, now on both surfaces).

The seventh oracle point is a NEGATIVE one and belongs to a test that already
exists: `tests/test_loc.py::TestGrammar::test_b6_prose_says_nothing` pins that a
`path:line` token sitting in PROSE says nothing about where the card is. This
unit deliberately does not touch it. A description may cite a file and a line as
EVIDENCE about somebody else's code rather than as this card's own location, and
the corpus in this file proves that is the common case rather than a hypothetical:
14 of its 26 real tokens name a file other than the card's own.
"""

from __future__ import annotations

import contextlib
import inspect
import subprocess
import sys

import pytest

from codebugs import db, findings, loc


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    """A real repository, because an anchor stores a commit and the file's text."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src" / "widget.py").write_text(
        "".join(f"line_{n} = 'a value long enough to anchor against'\n" for n in range(1, 61))
    )
    # Lines have to clear `loc.MIN_ANCHOR_CHARS`: below that floor a span is too
    # short to be searched for again after an edit, so capture declines rather
    # than store an anchor it could never re-find. `o7 = 7` does not clear it.
    (root / "src" / "other.py").write_text(
        "".join(f"other_value_{n} = 'a distinct body for line {n}'\n" for n in range(1, 61))
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


class _FakeMCP:
    """The registrar `server.py` passes in, reduced to what registration uses."""

    def __init__(self):
        self.tools = {}

    def tool(self, *_a, **_k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture()
def tools(repo, monkeypatch):
    """The REAL MCP tool objects, bound to a tracker inside the fixture repo.

    Registering them is the point: a test that called `findings.add_finding`
    directly could not see the wrapper, and the wrapper is the whole change.
    """
    monkeypatch.chdir(repo)
    db.set_tracker_root(str(repo))
    db.init_project(str(repo))

    @contextlib.contextmanager
    def conn_factory():
        conn = db.connect()
        try:
            yield conn
        finally:
            conn.close()

    mcp = _FakeMCP()
    findings.register_tools(mcp, conn_factory)
    return mcp.tools


def _add(tools, **over):
    kw = dict(
        severity="low",
        category="anchor_surface",
        file="src/widget.py",
        description="a description long enough that nothing deduplicates it by accident",
        new_category=True,
    )
    kw.update(over)
    return tools["add"](**kw)


# ---------------------------------------------------------------------------
# 1. The parameter reaches the anchor, through the MCP tool.
# ---------------------------------------------------------------------------


class TestTheParameterReachesTheAnchor:
    """MUTANT: drop the `_compose_add_meta` call from the `add` wrapper, so the
    parameter is accepted and discarded. Every test in this class goes red —
    which is the point, because without the call the tool still RETURNS a
    perfectly success-shaped result."""

    def test_a_named_line_anchors_the_card(self, tools):
        result = _add(tools, lines="12")
        assert result["meta"]["lines"] == "12"
        anchor = result["meta"]["loc"]
        assert anchor["path"] == "src/widget.py"
        assert anchor["line"] == 12
        assert anchor["end"] == 12
        assert anchor["text"] == ["line_12 = 'a value long enough to anchor against'"]
        assert anchor["commit"]

    def test_get_reports_the_card_as_anchored(self, tools):
        """The oracle names `anchor.state == "anchored"`, and that state is
        computed by a DIFFERENT tool than the one that captured it. Asserting
        the capture alone would leave the reader's half unpinned."""
        card = _add(tools, lines="12")
        fetched = tools["get"](finding_id=card["id"], resolve_anchors=True)
        assert fetched["anchor"]["state"] == "anchored"
        assert fetched["anchor"]["resolved"] is True

    def test_without_the_parameter_there_is_no_anchor(self, tools):
        """The other direction, and it is what makes the class non-vacuous: if
        cards anchored anyway, the parameter would be proving nothing.

        Note what "no anchor" looks like on disk. It is not an absent `loc` key:
        capture always records that it LOOKED, and stores `skipped: no_grammar`
        — nothing in this observation said where. `no_grammar` is what 194 of
        this tracker's 232 cards carry, and CB-233 exists because on the MCP
        surface there was no way to say otherwise."""
        result = _add(tools, description="a card that names no place in the code at all")
        assert result["meta"]["loc"]["skipped"] == "no_grammar"
        assert "path" not in result["meta"]["loc"]
        fetched = tools["get"](finding_id=result["id"], resolve_anchors=True)
        assert fetched["anchor"]["state"] != "anchored"

    def test_a_batch_member_anchors_from_its_own_lines(self, tools):
        results = tools["batch_add"](
            findings=[
                {
                    "severity": "low",
                    "category": "anchor_surface",
                    "file": "src/widget.py",
                    "description": "first member, anchored at its own named line",
                    "lines": "3",
                },
                {
                    "severity": "low",
                    "category": "anchor_surface",
                    "file": "src/other.py",
                    "description": "second member, a different file and a different line",
                    "lines": "40-42",
                },
                {
                    "severity": "low",
                    "category": "anchor_surface",
                    "file": "src/widget.py",
                    "description": "third member, which names no place in the code",
                },
            ],
            new_category=True,
        )
        assert results[0]["meta"]["loc"]["line"] == 3
        assert results[0]["meta"]["loc"]["path"] == "src/widget.py"
        assert results[1]["meta"]["loc"]["line"] == 40
        assert results[1]["meta"]["loc"]["end"] == 42
        assert results[1]["meta"]["loc"]["path"] == "src/other.py"
        # A member's location is its own: the third must not inherit either.
        assert results[2]["meta"]["loc"]["skipped"] == "no_grammar"
        assert "path" not in results[2]["meta"]["loc"]


# ---------------------------------------------------------------------------
# 2. The tokens people actually write.
# ---------------------------------------------------------------------------

# Harvested 2026-08-27 from `export-csv` over this repository's live tracker:
# every distinct `path:line` / `path:line-line` token appearing in the
# description of a real card, paired with THAT CARD's own `file` column. The
# tracker held 233 cards and 231 distinct such tokens; these 26 are a spread
# across full paths, bare basenames, single lines, ranges, shell scripts and
# markdown — including the five the CB-233 card names by hand.
#
# The last field is whether the token's path names the CARD's own file, and the
# split is 12 agreeing / 14 not. Both halves are load-bearing. A corpus where
# every token agreed could not tell a correct `select_site` from one that
# ignored the `file` column entirely, and 14 rows here are the ordinary case the
# design is built around: a description citing a file and a line as EVIDENCE
# about another module, which must NOT become this card's anchor.
#
# (card, that card's `file` column, token, path, line, end, names the card's own file)
CORPUS: tuple[tuple[str, str, str, str, int, int, bool], ...] = (
    ("CB-233", "src/codebugs/loc.py", "src/codebugs/loc.py:1850", "src/codebugs/loc.py", 1850, 1850, True),
    ("CB-233", "src/codebugs/loc.py", "src/codebugs/db.py:924", "src/codebugs/db.py", 924, 924, False),
    ("CB-233", "src/codebugs/loc.py", "merge.py:82", "merge.py", 82, 82, False),
    ("CB-233", "src/codebugs/loc.py", "tools/worktree-finish.sh:719", "tools/worktree-finish.sh", 719, 719, False),
    ("CB-233", "src/codebugs/loc.py", "tools/cascade-mint.sh:364", "tools/cascade-mint.sh", 364, 364, False),
    ("CB-233", "src/codebugs/loc.py", "src/codebugs/loc.py:314", "src/codebugs/loc.py", 314, 314, True),
    ("CB-233", "src/codebugs/loc.py", "src/codebugs/findings.py:1429", "src/codebugs/findings.py", 1429, 1429, False),
    ("CB-230", "src/codebugs/loc.py", "findings.py:3397", "findings.py", 3397, 3397, False),
    ("CB-230", "src/codebugs/loc.py", "findings.py:919", "findings.py", 919, 919, False),
    ("CB-195", "src/codebugs/merge.py", "merge.py:82-86", "merge.py", 82, 86, True),
    ("CB-195", "src/codebugs/merge.py", "milestones/_schema.py:159-172", "milestones/_schema.py", 159, 172, False),
    ("CB-138", "tools/cascade-mint.sh", "tools/cascade-mint.sh:364-366", "tools/cascade-mint.sh", 364, 366, True),
    ("CB-138", "tools/cascade-mint.sh", "tests/test_cascade_mint.py:83", "tests/test_cascade_mint.py", 83, 83, False),
    ("CB-134", "src/codebugs/cli.py", "cli.py:106", "cli.py", 106, 106, True),
    ("CB-134", "src/codebugs/cli.py", "argparse.py:2752", "argparse.py", 2752, 2752, False),
    ("CB-113", "src/codebugs/findings.py", "findings.py:902", "findings.py", 902, 902, True),
    ("CB-113", "src/codebugs/findings.py", "findings.py:900-905", "findings.py", 900, 905, True),
    ("CB-88", "src/codebugs/provenance.py", "provenance.py:105-125", "provenance.py", 105, 125, True),
    ("CB-88", "src/codebugs/provenance.py", "src/codebugs/provenance.py:117-123", "src/codebugs/provenance.py", 117, 123, True),
    ("CB-50", "tools/worktree-setup.sh", "CLAUDE.md:8", "CLAUDE.md", 8, 8, False),
    ("CB-50", "tools/worktree-setup.sh", "CLAUDE.md:16", "CLAUDE.md", 16, 16, False),
    ("CB-43", "src/codebugs/findings.py", "findings.py:142-190", "findings.py", 142, 190, True),
    ("CB-43", "src/codebugs/findings.py", "sweep.py:264-278", "sweep.py", 264, 278, False),
    ("CB-16", "src/codebugs/findings.py", "src/codebugs/findings.py:264-285", "src/codebugs/findings.py", 264, 285, True),
    ("CB-15", "src/codebugs/findings.py", "src/codebugs/findings.py:240-273", "src/codebugs/findings.py", 240, 273, True),
    ("CB-213", "CLAUDE.md", "src/codebugs/merge.py:114", "src/codebugs/merge.py", 114, 114, False),
)


class TestRealCorpusTokens:
    """MUTANT: narrow the parameter to an integer, or stop forwarding it. Every
    parametrized case goes red. Synthetic `foo.py:1` tokens would survive both
    the narrowing and several plausible half-fixes, which is why these are real."""

    def test_the_corpus_is_not_quietly_one_sided(self):
        """A guard on the fixture itself. If somebody trims this table down to
        the agreeing rows, the two classes below stop discriminating anything
        and would still be green — the vacuous-fixture shape."""
        agree = sum(1 for row in CORPUS if row[6])
        assert agree == 12, agree
        assert len(CORPUS) - agree == 14

    @pytest.mark.parametrize("card,column,token,path,line,end,owns", CORPUS, ids=[r[2] for r in CORPUS])
    def test_every_real_token_parses_into_its_place(
        self, tools, card, column, token, path, line, end, owns
    ):
        """Through the NEW parameter, on the REAL tool: the token a person wrote
        must come back out as the place they meant."""
        result = _add(
            tools,
            file=column,
            description=f"a real token harvested from {card}: {token}",
            lines=token,
        )
        sites = loc.parse_sites(result["meta"])
        assert sites == [(path, line, end)]

    @pytest.mark.parametrize("card,column,token,path,line,end,owns", CORPUS, ids=[r[2] for r in CORPUS])
    def test_a_token_naming_another_file_does_not_anchor_this_card(
        self, tools, card, column, token, path, line, end, owns
    ):
        """The design's own refusal, reached through the new parameter: a site
        whose path is not this card's file is `no_matching_site`, not an excuse
        to read another file's line numbers against this one."""
        result = _add(
            tools,
            file=column,
            description=f"selection over a real token from {card}: {token}",
            lines=token,
        )
        selected = loc.select_site(loc.parse_sites(result["meta"]), column)
        assert (selected is not None) is owns


# ---------------------------------------------------------------------------
# 3. Four spellings.
# ---------------------------------------------------------------------------


class TestFourSpellings:
    """MUTANT: type the parameter `int` (or coerce it), and the range, the full
    token and the list cases go red while the bare number stays green — which is
    exactly why one case would not have been enough."""

    def test_bare_number(self, tools):
        result = _add(tools, lines="12", description="bare number spelling of a place")
        assert loc.parse_sites(result["meta"]) == [(None, 12, 12)]

    def test_range(self, tools):
        result = _add(tools, lines="12-15", description="range spelling of a place")
        assert loc.parse_sites(result["meta"]) == [(None, 12, 15)]

    def test_full_token_carries_its_own_path(self, tools):
        result = _add(
            tools, lines="src/widget.py:12", description="full token spelling of a place"
        )
        assert loc.parse_sites(result["meta"]) == [("src/widget.py", 12, 12)]

    def test_list_is_n_separate_lines_and_never_a_range(self, tools):
        """The grammar's B2 contract, inherited rather than re-decided here: a
        list is N sites, and `[12, 15]` must not silently widen into 12..15."""
        result = _add(tools, lines=[12, 15], description="list spelling of two places")
        assert loc.parse_sites(result["meta"]) == [(None, 12, 12), (None, 15, 15)]


# ---------------------------------------------------------------------------
# 4. ONE rule, both surfaces.
# ---------------------------------------------------------------------------

# Each row is one input, expressed once, and run through BOTH surfaces. This is
# the table whose whole purpose is to go red if the two ever answer differently:
# CB-129's refusal used to live as a passage of code inside `_cmd_add`, and
# CB-233 gave it a second caller, at which point a copy would have been one edit
# from disagreeing with the original.
#
# (label, the `lines` value, the `meta` mapping, refused?)
BOTH_SURFACES: tuple[tuple[str, object, dict | None, bool], ...] = (
    ("neither supplied", None, None, False),
    ("only the named input", "10-20", None, False),
    ("only the meta key", None, {"lines": "10-20"}, False),
    ("both, equal", "10-20", {"lines": "10-20"}, False),
    ("both, different values", "10-20", {"lines": "30-40"}, True),
    ("both, different types", "10-20", {"lines": [10, 20]}, True),
    ("named input beside an unrelated meta key", "10-20", {"module": "m"}, False),
    ("an explicitly empty named input", "", None, True),
    ("an explicitly empty named input, with meta", "", {"lines": "10-20"}, True),
    # CB-236. A SIBLING key of another spelling, which the rows above cannot
    # reach: every one of them varies the `lines` key alone, so none of them
    # crosses the grammar's priority table. Each of these four must answer the
    # same on both surfaces for the same reason the originals must.
    ("a sibling singular key naming another place", "10", {"line": "3"}, True),
    ("a sibling singular key naming the same place", "10", {"line": "10"}, False),
    ("a sibling key the grammar reads no place from", "10", {"sites": ["fn_a", "fn_b"]}, False),
    ("a named input that names no place, beside a sibling that does", "abc", {"line": "3"}, True),
)


def _cli_verdict(tmp_project, monkeypatch, lines, meta):
    """Run one row through the CLI surface. Returns (refused, stored meta)."""
    import json

    from codebugs import cli

    argv = [
        "codebugs", "--tracker-root", str(tmp_project), "add",
        "-s", "low", "-c", "surface", "-f", "f.py",
        "-d", "one input, run through both surfaces, for comparison",
        "--new-category",
    ]
    if lines is not None:
        argv += ["-l", lines if isinstance(lines, str) else json.dumps(lines)]
    if meta is not None:
        argv += ["--meta", json.dumps(meta)]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        cli.main()
    except SystemExit as exc:
        return (exc.code != 0), None
    conn = db.connect(str(tmp_project))
    try:
        rows = findings.query_findings(conn)["findings"]
        return False, (rows[0]["meta"] or {}) if rows else {}
    finally:
        conn.close()


def _mcp_verdict(tools, lines, meta, label):
    """The same row through the MCP surface. Returns (refused, stored meta)."""
    kw: dict = {}
    if lines is not None:
        kw["lines"] = lines
    if meta is not None:
        kw["meta"] = meta
    try:
        result = _add(tools, description=f"both-surface row: {label}", **kw)
    except ValueError:
        return True, None
    return False, result["meta"] or {}


class TestOneRuleOnBothSurfaces:
    """MUTANT: give either surface its own copy of the conflict check and let one
    of the two spellings win silently there. `test_the_two_surfaces_agree` goes
    red on the row that diverged — and it is the ONLY test that could, because
    each surface on its own would still be internally consistent."""

    @pytest.mark.parametrize("label,lines,meta,refused", BOTH_SURFACES, ids=[r[0] for r in BOTH_SURFACES])
    def test_the_two_surfaces_agree(self, tools, tmp_path, monkeypatch, label, lines, meta, refused):
        project = tmp_path / f"cli-{abs(hash(label))}"
        project.mkdir()
        db.init_project(str(project))
        cli_refused, cli_meta = _cli_verdict(project, monkeypatch, lines, meta)
        mcp_refused, mcp_meta = _mcp_verdict(tools, lines, meta, label)

        assert cli_refused == refused, f"CLI disagrees with the declared verdict on {label!r}"
        assert mcp_refused == refused, f"MCP disagrees with the declared verdict on {label!r}"
        if not refused:
            # And when both accept, they must have stored the SAME field value.
            # A rule that refuses identically while storing differently is still
            # two rules.
            assert cli_meta.get("lines") == mcp_meta.get("lines"), label

    def test_the_refusal_names_both_spellings_on_each_surface(self, tools, tmp_path, monkeypatch, capsys):
        """A refusal that does not say WHICH two inputs collided leaves the
        caller to guess which of its lines to delete. Each surface must name the
        pair in its OWN vocabulary — `--lines`/`--meta` for a shell, the bare
        parameter names for a tool call."""
        project = tmp_path / "naming"
        project.mkdir()
        db.init_project(str(project))
        refused, _ = _cli_verdict(project, monkeypatch, "10-20", {"lines": "30-40"})
        assert refused
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--lines" in err and "--meta" in err
        assert "10-20" in err and "30-40" in err

        with pytest.raises(ValueError) as exc:
            _add(tools, lines="10-20", meta={"lines": "30-40"}, description="mcp refusal text")
        message = str(exc.value)
        assert "Traceback" not in message
        assert "lines" in message and "meta" in message
        assert "10-20" in message and "30-40" in message
        # And it must NOT tell a tool caller to edit a command-line flag it
        # never typed.
        assert "--lines" not in message and "--meta" not in message

    def test_a_batch_refusal_names_the_member(self, tools):
        """`lines and meta.lines disagree` says nothing useful about a batch of
        thirty. The index is the only thing that makes the refusal actionable."""
        member = {
            "severity": "low",
            "category": "anchor_surface",
            "file": "src/widget.py",
            "description": "the offending member of the batch",
            "lines": "10",
            "meta": {"lines": "20"},
        }
        clean = {
            "severity": "low",
            "category": "anchor_surface",
            "file": "src/widget.py",
            "description": "a perfectly ordinary member ahead of it",
        }
        with pytest.raises(ValueError) as exc:
            tools["batch_add"](findings=[clean, clean, member], new_category=True)
        assert "findings[2]" in str(exc.value)

    def test_the_rule_is_constructed_once(self):
        """The anti-drift check proper, and it is structural because behaviour
        cannot see the difference between one shared rule and two identical
        copies. `_compose_add_meta` must be the ONLY reader of the declaration:
        a second site looping over `_ADD_META_FLAGS` is a second rule, whatever
        it happens to say on the day it is written."""
        import ast
        import pathlib

        source = pathlib.Path(findings.__file__).read_text()
        tree = ast.parse(source)
        readers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "_ADD_META_FLAGS":
                # Which enclosing function mentions it?
                readers.add(node.lineno)
        assert readers, "the declaration must still be read by something"

        # Every mention must sit inside `_compose_add_meta`, apart from the
        # assignment that declares it and the tests' own introspection (which
        # lives in another file).
        composer = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_compose_add_meta"
        )
        declaration = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "_ADD_META_FLAGS"
        )
        allowed = set(range(composer.lineno, composer.end_lineno + 1)) | {declaration.lineno}
        stray = sorted(line for line in readers if line not in allowed)
        assert not stray, (
            f"`_ADD_META_FLAGS` is read outside `_compose_add_meta` at lines {stray}: "
            "that is a second copy of the rule."
        )


# ---------------------------------------------------------------------------
# 4b. CB-236 — the named input must be the place that RESULTS, not merely a key
#     nobody contradicted under its own name.
# ---------------------------------------------------------------------------


class TestTheNamedInputMustDecideTheAnchor:
    """CB-236. The rule above this one compares the named input against the meta
    key of its OWN name, and the capture grammar prefers the SINGULAR spellings —
    so `lines=10` beside `meta.line=3` satisfied it and the anchor still landed on
    3, at exit 0, with the named input stored beside it and lost.

    WHY THIS CLASS AND NOT MORE ROWS IN `BOTH_SURFACES`: those rows read the
    stored `meta` and the verdict, which is where the DEFECT hid — both keys were
    stored, so meta looked right. The discriminator is the ANCHOR, so these tests
    read `meta.loc` instead.

    HOW THE QUESTION TRAVELS, because it is not the obvious route and the obvious
    route is refused: the surface does NOT read the grammar. `findings` may not
    import the extension — the dependency runs the other way, so a module-level
    import is a measured `ImportError` and a deferred one is refused by
    `tests/test_loc_read_paths.py::TestThereIsExactlyOneResolver`. The extension
    declares itself able to answer, and `db.named_input_conflict` asks it.

    MUTANTS RUN, AND WHICH PIN CAUGHT EACH (measured 2026-08-29 over this file,
    `tests/test_loc.py` and `tests/test_loc_read_paths.py`; 291 green unmutated).
    The point of the table is that no single pin covers the class and none of
    them is decoration:

    * **the surface stops asking** (the defect restored) — `..._naming_another_
      place_is_refused` and `..._names_both_places_and_the_key_that_wins`, plus
      two `BOTH_SURFACES` rows. 4 red.
    * **the extension stops answering** (`_named_input_conflict` returns `None`)
      — the same 4, plus `..._the_seam_loads_the_extension_before_answering`. 5
      red. Two halves, two ways to break it, and both are covered.
    * **the TEMPTING WRONG FORM: refuse whenever any other priority key is
      present** — the enumeration the brief forbids. Every pin above stays GREEN;
      what goes red is `..._two_keys_naming_one_place_are_not_a_conflict` and
      `..._a_silent_sibling_key_still_lets_the_named_input_anchor`, i.e. exactly
      the two boundaries, plus both anti-drift pins — this repository's own
      resolver-boundary pin among them, since that form has to import the
      extension to be written at all. 7 red.
    * **the seam stops loading the modules before answering** — a GATE THAT
      CANNOT FIRE, silent on every CLI process. Exactly ONE pin sees it,
      `..._the_seam_loads_the_extension_before_answering`, and it exists for that.
    * **the refusal text stops naming the deciding key** — again exactly ONE,
      `..._names_both_places_and_the_key_that_wins`.

    Two tests here stay green on every mutant and are PRESERVATION pins rather
    than broken ones, which this file says out loud because a reader cannot
    otherwise tell the difference: `..._anchors_when_it_is_the_only_thing_naming_
    a_place` is the baseline the whole rule is stated against, and
    `..._keeps_the_grammars_own_priority` holds the SCOPE — a row with no named
    input must not be touched.
    """

    def _anchor(self, tools, **over):
        return (_add(tools, **over)["meta"] or {}).get("loc") or {}

    def test_the_named_input_anchors_when_it_is_the_only_thing_naming_a_place(self, tools):
        assert self._anchor(tools, lines="10").get("line") == 10

    def test_a_sibling_singular_key_naming_another_place_is_refused(self, tools):
        """The defect itself. Before the fix this returned a card anchored at 3."""
        with pytest.raises(ValueError):
            _add(tools, lines="10", meta={"line": "3"})

    def test_two_keys_naming_one_place_are_not_a_conflict(self, tools):
        """Agreement through a DIFFERENT spelling still passes, and still anchors
        where both of them said. A rule keyed on 'is another priority key here'
        cannot tell this from the test above it."""
        assert self._anchor(tools, lines="10", meta={"line": "10"}).get("line") == 10

    def test_a_silent_sibling_key_still_lets_the_named_input_anchor(self, tools):
        """The measured `CB-80` class: a `sites` value carrying FUNCTION NAMES.
        The grammar reads no place out of it, so it takes no anchor and there is
        nothing to conflict with — the named input decides, as it should."""
        anchor = self._anchor(tools, lines="10", meta={"sites": ["handle_a", "handle_b"]})
        assert anchor.get("line") == 10

    def test_a_row_with_no_named_input_keeps_the_grammars_own_priority(self, tools):
        """SCOPE, stated as a test because a scope claimed only in prose is the
        thing this repository keeps being wrong about. Two meta keys and NO named
        input is untouched by this rule: nothing was named, so nothing can be
        disappointed, and `line` still wins exactly as `_KEY_PRIORITY` says."""
        assert self._anchor(tools, meta={"lines": "10", "line": "3"}).get("line") == 3

    def test_the_refusal_names_both_places_and_the_key_that_wins(self, tools):
        """§4.3. The measurement that licensed comparing places without first
        normalizing them is a sample of two rows out of 3732, so a false refusal
        must cost one edit rather than an investigation: the text has to carry
        what each side gave AND which key won."""
        with pytest.raises(ValueError) as exc:
            _add(tools, lines="10", meta={"line": "3"})
        message = str(exc.value)
        assert "line 10" in message, "the named input's place is missing"
        assert "line 3" in message, "the place meta would produce is missing"
        assert "'line'" in message, "the winning key is not named"
        assert "Traceback" not in message

    def test_the_seam_loads_the_extension_before_answering(self):
        """A GATE THAT CANNOT FIRE, reintroduced by the route the question takes.

        `_compose_add_meta` runs at the SURFACE, before `connect()`, so in a fresh
        CLI process nothing has imported the extension that owns the precedence
        and the registry is genuinely EMPTY. Without the module load inside
        `db.named_input_conflict` the answer would be "no conflict" — silent on
        every CLI invocation and loud only under a server that had already
        imported everything, which is the exact hazard
        `db.resolver_reserved_meta_keys` records having hit once before.

        Run in a SUBPROCESS because the fact being pinned is about a process that
        has imported nothing else, and this one has imported the whole suite.
        `PYTHONPATH` is set explicitly: from a worktree a bare interpreter would
        resolve `codebugs` through the main checkout's editable install and
        measure a tree this run did not touch.
        """
        import pathlib

        src = pathlib.Path(findings.__file__).parents[1]
        probe = (
            "from codebugs import db\n"
            "print('EMPTY' if not db._named_input_checks else 'PREloaded')\n"
            "print(db.named_input_conflict('lines', 10, {'lines': 10, 'line': 3}) is not None)\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
        )
        before, answered = out.stdout.split()
        # The first line is what makes the second one evidence rather than luck:
        # if something had already imported the extension, the seam's own load
        # would prove nothing.
        assert before == "EMPTY", f"the registry was already populated: {out.stdout!r}"
        assert answered == "True", (
            "the seam answered 'no conflict' in a process that had not imported "
            "the extension — the module load inside `named_input_conflict` is "
            f"what stops that, and it is missing. Raw output: {out.stdout!r}"
        )

    def test_the_rule_asks_the_grammar_rather_than_copying_its_table(self):
        """ANTI-DRIFT, and it is the whole reason the check is shaped as
        'would my place result' rather than 'which keys are present'. A list of
        priority keys inside `findings.py` would be a second copy of
        `loc._KEY_PRIORITY`, and it would disagree with the first the day the
        order changes — which is the failure the grammar's own comment describes.

        READ BY AST, AND THAT IS NOT TIDINESS. The first draft of this test
        searched the file's TEXT and went red on this fix's own prose, which
        names `loc._KEY_PRIORITY` in a docstring precisely in order to explain
        itself. Same shape as `CB-139`'s `fetch-depth` matcher: a text search
        answers a different question from the one being asked, and it is wrong in
        BOTH directions — it flags an explanation, and a real copy hidden behind
        a `getattr` would sail past it. What is asked here is what the CODE does.
        """
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(findings.__file__).read_text())
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        names = {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        assert "_KEY_PRIORITY" not in names, (
            "`findings.py` READS the grammar's priority table: that is a second "
            "copy of the rule. Ask `loc.is_location_key` instead."
        )
        # `lines` is excluded because the named input legitimately owns that key
        # and `_ADD_META_FLAGS` declares it — that is the declaration, not a copy
        # of the grammar's ordering.
        forbidden = {k for k in loc._KEY_PRIORITY if k != "lines"}
        spelled = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
        }
        assert not (forbidden & spelled), (
            f"`findings.py` spells the grammar's {sorted(forbidden & spelled)} key(s) "
            "as a literal: the check must ask `loc.is_location_key` rather than "
            "enumerate what the grammar reads."
        )


# ---------------------------------------------------------------------------
# 5. A bare number rides on the `file` column.
# ---------------------------------------------------------------------------


class TestBareNumberUsesTheFileColumn:
    """MUTANT: require a path inside the token (refuse a bare number, or refuse
    to fall back to the `file` column). Both tests go red.

    Why this matters in practice rather than in principle: measured over this
    repository's tracker on 2026-08-27, 51 of 51 LIVE cards carry a `file`
    column that resolves to a real file in the checkout (229 of all 233 rows do).
    So the path is nearly always already on the card, and a bare number is the
    cheapest thing a filer can possibly be asked to add."""

    def test_a_bare_number_anchors_against_the_file_column(self, tools):
        result = _add(tools, file="src/other.py", lines="7", description="bare number, other file")
        assert result["meta"]["loc"]["path"] == "src/other.py"
        assert result["meta"]["loc"]["line"] == 7
        assert result["meta"]["loc"]["text"] == ["other_value_7 = 'a distinct body for line 7'"]

    def test_a_bare_range_anchors_against_the_file_column(self, tools):
        result = _add(tools, file="src/other.py", lines="7-9", description="bare range, other file")
        assert result["meta"]["loc"]["path"] == "src/other.py"
        assert (result["meta"]["loc"]["line"], result["meta"]["loc"]["end"]) == (7, 9)


# ---------------------------------------------------------------------------
# 6. Not supplied is not the same as empty.
# ---------------------------------------------------------------------------


class TestNotSuppliedIsNotEmpty:
    """CB-133's rule, now on the MCP surface too. MUTANT: guard the parameter
    with truthiness (`if lines:`) instead of `is None`, and the refusal tests go
    green-to-red — an explicitly empty value would land nowhere and report
    success, which is CB-129's own success-shaped discard surviving on the empty
    value.

    On a WRITE path `None` is the only "not supplied" (CB-82). The query-side
    rule that `""` ALSO means absent (CB-25) is deliberately the opposite one and
    must not be borrowed here: an absent stored value means *invent one*, and
    nobody ever meant an empty line range."""

    def test_none_is_not_supplied_and_lands_nothing(self, tools):
        result = _add(tools, lines=None, description="a card that supplies no location")
        assert "lines" not in (result["meta"] or {})
        assert result["meta"]["loc"]["skipped"] == "no_grammar"

    def test_folding_nothing_into_nothing_yields_nothing(self):
        """`meta=None` must travel on as `None`, not become `{}`. The result of
        the call cannot show this — every stored card acquires machinery keys of
        its own — so it is asserted where the decision is made. Handing a domain
        function `{}` where the caller passed `None` is a different value, and
        the two are not interchangeable everywhere in this package."""
        composed = findings._compose_add_meta(
            {}.get, None, spelling=lambda p, _c: p, meta_spelling="meta", prefix="add"
        )
        assert composed is None
        untouched = {"module": "m"}
        assert (
            findings._compose_add_meta(
                {}.get, untouched, spelling=lambda p, _c: p, meta_spelling="meta", prefix="add"
            )
            is untouched
        )

    @pytest.mark.parametrize("empty", ["", []], ids=["empty string", "empty list"])
    def test_an_explicitly_empty_value_is_refused(self, tools, empty):
        with pytest.raises(ValueError) as exc:
            _add(tools, lines=empty, description=f"an explicitly empty location {empty!r}")
        assert "empty value" in str(exc.value)

    def test_a_refusal_stores_nothing(self, tools):
        """A refusal must cost no partial work (CB-82). It runs before the
        connection is opened, and this is the observable form of that."""
        before = tools["query"](limit=1)["total"]
        with pytest.raises(ValueError):
            _add(tools, lines="", description="a refusal that must leave no trace")
        assert tools["query"](limit=1)["total"] == before

    def test_an_empty_batch_member_value_is_refused_by_index(self, tools):
        with pytest.raises(ValueError) as exc:
            tools["batch_add"](
                findings=[
                    {
                        "severity": "low",
                        "category": "anchor_surface",
                        "file": "src/widget.py",
                        "description": "a member whose location is explicitly empty",
                        "lines": "",
                    }
                ],
                new_category=True,
            )
        assert "findings[0]" in str(exc.value)


# ---------------------------------------------------------------------------
# What the new input must NOT disturb: identity.
# ---------------------------------------------------------------------------


class TestIdentityIsUnaffected:
    """The failure mode nobody asked about, and the one that would hurt most.

    A finding's identity is derived from its category, its file and its
    NORMALIZED description — and that normalization strips the observation's own
    `meta` STRING VALUES out of the text before hashing (CB-43 item 4). So on
    paper, adding `meta.lines` to an observation could change what gets hashed,
    and the day filers started passing the new parameter every card in the
    tracker would be filed a second time as a new row instead of bumping the one
    that is already there.

    Measured on this branch: it does not. The fingerprint is identical with the
    parameter, without it, with a DIFFERENT value in it, and whether or not the
    description happens to quote the same number. These tests exist so that stays
    true rather than being rediscovered on a live tracker.

    **WHICH DEDUP BRANCHES ARE ACTUALLY PINNED HERE, because the claim above is
    wider than the check below and saying so is cheaper than implying otherwise.**
    Findings have four dedup outcomes (CB-43): `created`, `bumped`, `reopened`
    (a hit on a `fixed` row) and `recurrence_of_closed` (a hit on a dismissed
    one). This class exercises the first TWO. The other two are NOT covered by
    any test in this file, and no test elsewhere covers them for this parameter
    either. The argument that they are safe is a structural one rather than a
    measured one — the parameter is consumed at the surface, before
    `add_finding`, so every branch below receives an observation that differs
    only in `meta`, and the fingerprint tests above show that difference does not
    reach identity — but a structural argument is what this repository routinely
    finds to be true of the elements and false of their composition. Read this
    class as covering the live-row half, and treat the terminal half as
    reasoned-about rather than pinned."""

    def _fingerprint(self, tools, description, **kw):
        return _add(tools, description=description, **kw)["fingerprint"]

    def test_naming_a_line_does_not_change_the_fingerprint(self, tools):
        clean = "the retry loop misreads the deadline and reports success anyway"
        assert self._fingerprint(tools, clean) == self._fingerprint(tools, clean, lines="12")

    def test_nor_does_it_when_the_description_quotes_the_same_number(self, tools):
        """The specific shape the meta-stripping rule makes plausible: the number
        appears both in the text and in the field."""
        quoted = "the retry loop misreads the deadline at line 12 and reports success"
        assert self._fingerprint(tools, quoted) == self._fingerprint(tools, quoted, lines="12")
        assert self._fingerprint(tools, quoted) == self._fingerprint(tools, quoted, lines="999")

    def test_the_second_filing_bumps_rather_than_creating(self, tools):
        """The property the fingerprints above only imply. This is what a filer
        adopting the new parameter actually does: files a finding it has filed
        before, now saying where it is."""
        text = "a defect reported once without a location and once with one"
        first = _add(tools, description=text)
        second = _add(tools, description=text, lines="12")
        assert second["id"] == first["id"]
        assert second["was_new"] is False
        assert second["dedup_action"] == "bumped"

    def test_a_bumped_card_keeps_its_own_anchor(self, tools):
        """BT-4: top-level `meta` is observation-FROZEN — a re-observation's meta
        lands in the occurrence ring, not on the row. So a second observation
        naming a DIFFERENT line must not silently move the card's anchor onto it.
        Verified rather than taken on faith, because this is the one place the new
        input could rewrite something a human decided."""
        text = "a defect whose anchor must not move under a later observation"
        first = _add(tools, description=text, lines="12")
        assert first["meta"]["loc"]["line"] == 12
        second = _add(tools, description=text, lines="40")
        assert second["id"] == first["id"]
        assert second["meta"]["loc"]["line"] == 12, "the later observation moved the anchor"
        assert second["meta"]["lines"] == "12"

    def test_the_first_filing_may_still_supply_the_anchor(self, tools):
        """The other direction, and it is what stops the previous test from
        reading as 'the parameter does nothing on a bump'. A card filed WITHOUT a
        location does not acquire one from a later observation either — the ring
        records it, the row does not — which is the honest, and slightly
        disappointing, consequence of the freeze."""
        text = "a defect filed first with no location at all, then with one"
        first = _add(tools, description=text)
        assert first["meta"]["loc"]["skipped"] == "no_grammar"
        second = _add(tools, description=text, lines="12")
        assert second["id"] == first["id"]
        assert second["meta"]["loc"]["skipped"] == "no_grammar"
        # The observation is not lost, it is just not the row's authored state.
        ring = second["meta"].get("occurrences") or []
        assert any((entry.get("meta") or {}).get("lines") == "12" for entry in ring), ring


# ---------------------------------------------------------------------------
# The surface declaration itself.
# ---------------------------------------------------------------------------


class TestTheSurfaceDeclaresIt:
    """A parameter that exists in the domain layer is not reachable until it is
    declared here (CB-18, the `append_note` precedent this unit repeats). These
    are the cheap structural pins that a rename or a signature edit would break
    before anyone noticed the tool had gone quiet again."""

    def test_the_declaration_names_the_parameter_every_surface_uses(self):
        """One spelling, not three. The table's first field is the parameter
        name on EVERY surface — argparse dest and MCP parameter alike — and its
        second is the meta key the grammar reads."""
        assert findings._ADD_META_FLAGS == (("lines", "lines", "-l/--lines"),)

    def test_the_mcp_add_tool_declares_it(self):
        mcp = _FakeMCP()
        findings.register_tools(mcp, lambda: None)
        sig = inspect.signature(mcp.tools["add"])
        for param, _key, _cli in findings._ADD_META_FLAGS:
            assert param in sig.parameters, f"MCP `add` does not declare {param!r}"
            assert sig.parameters[param].default is None

    def test_the_cli_add_parser_declares_it(self):
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        findings.register_cli(sub, {})
        dests = {a.dest for a in sub.choices["add"]._actions}
        for param, _key, spelling in findings._ADD_META_FLAGS:
            assert param in dests, f"`{spelling}` is not an argument of `codebugs add`"

    def test_the_descriptions_say_what_the_field_is_for(self):
        """The half of this unit no mutant can reach, and the half the whole
        thing turns on: a client reads these words and decides whether to pass
        the field. Before CB-233 the `meta` description listed `lines` as a
        throwaway example beside `module` and `rule_code`, which gave a filer no
        reason to supply the one input that produces an anchor."""
        mcp = _FakeMCP()
        findings.register_tools(mcp, lambda: None)
        for name in ("add", "batch_add"):
            doc = inspect.getdoc(mcp.tools[name]) or ""
            assert "anchor" in doc.lower(), f"{name} never says what `lines` is for"
        add_doc = inspect.getdoc(mcp.tools["add"]) or ""
        assert "lines, module, rule_code" not in add_doc, (
            "the `meta` description still presents the location as a throwaway example"
        )

        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        findings.register_cli(sub, {})
        lines_help = next(
            a.help for a in sub.choices["add"]._actions if a.dest == "lines"
        )
        assert "stored in meta" not in lines_help, (
            "the CLI help still says where the value is PUT rather than what it is FOR"
        )
        assert "anchor" in lines_help.lower()
