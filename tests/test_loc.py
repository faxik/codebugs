"""Location anchor capture (BT-7 Т-a, CB-95).

Four things are being pinned here, and only the first is ordinary coverage.

1. The grammar (§4) and the Р2 object, both directions.
2. Р8's promise, which is the one that cannot be kept by prose: EVERY capture
   refusal must arrive as a stored refusal object and NEVER as
   `meta.resolver_errors`. `db.run_pre_add_resolvers` catches everything a
   resolver raises — including out of `_validate_resolver_outcome`, which runs
   inside the same catch — so the only way this holds is if the module
   classifies its own failures first. One test per token, plus the classifier's
   ordering.
3. §7.3's lock argument, in both halves: the PREMISE that a writer locked out
   past `busy_timeout` gets an unclassifiable `SQLITE_BUSY`, and the RELATION
   that the capture budget is well under it.
4. The behavioural shift this unit itself introduces: `add` starts refusing a
   caller-supplied `meta.loc`, `update` starts accepting one, and import strips
   it — none of which any earlier test could have covered, because the key did
   not exist.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sqlite3
import subprocess
import threading
import time

import pytest

from codebugs import db, findings, loc


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    """A real repository with the shapes capture has to classify.

    Committed: a plain text file, a SUBDIRECTORY (the case `git show` gets wrong
    — see `loc.read_blob`), a binary blob, and a file with one very long line.
    """
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text(
        "import os\n"
        "\n"
        "def compute_the_thing(argument):\n"
        "    return argument * 2\n"
        "\n"
        "x = 1\n"
    )
    (root / "sub" / "g.py").write_text("y = 2\n")
    (root / "b.bin").write_bytes(b"\x00\x01binary\x02")
    (root / "long.py").write_text("L = '" + "z" * 4000 + "'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture()
def head(repo):
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True
    )
    return out.stdout.decode().strip()


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


def _add(conn, *, meta, file="f.py", commit=None, project_dir=None, **over):
    """One observation through the real add path; returns its stored meta."""
    kw = dict(
        severity="low",
        category="anchor_test",
        file=file,
        description="a description long enough not to be deduplicated by accident",
        meta=meta,
        reported_at_commit=commit,
        project_dir=project_dir,
        new_category=True,
    )
    kw.update(over)
    return findings.add_finding(conn, **kw)["meta"]


def _anchor(conn, **kw):
    return _add(conn, **kw)["loc"]


# --- the grammar (§4) -----------------------------------------------------------------


class TestGrammar:
    """Branch coverage over §4's forms, at the parse layer.

    Sites are compared as tuples rather than through capture so the grammar is
    pinned independently of whether a repository can serve the file.
    """

    def test_b1_bare_int_and_int_string(self):
        assert loc.parse_sites({"line": 12}) == [(None, 12, 12)]
        assert loc.parse_sites({"line": "12"}) == [(None, 12, 12)]

    def test_b1_a_bool_is_not_a_line_number(self):
        # `isinstance(True, int)` is True, so a flag would silently anchor line 1.
        assert loc.parse_sites({"line": True}) == []

    def test_b2_a_list_of_ints_is_n_separate_sites_never_a_range(self):
        # libcheck's contract, 206/206 in the corpus. Inferring 10-12 here would
        # widen an anchor whose producer meant three independent places.
        assert loc.parse_sites({"lines": [10, 11, 12]}) == [
            (None, 10, 10),
            (None, 11, 11),
            (None, 12, 12),
        ]

    def test_b3_path_token_as_string_list_and_dict(self):
        assert loc.parse_sites({"site": "src/a.py:14"}) == [("src/a.py", 14, 14)]
        assert loc.parse_sites({"sites": ["a.py:1", "b.py:2-4"]}) == [
            ("a.py", 1, 1),
            ("b.py", 2, 4),
        ]
        assert loc.parse_sites({"sites": {"a.py": "3-5"}}) == [("a.py", 3, 5)]

    def test_b4_bare_spec(self):
        assert loc.parse_sites({"lines": "10-12"}) == [(None, 10, 12)]
        assert loc.parse_sites({"lines": "3,7,20-22"}) == [
            (None, 3, 3),
            (None, 7, 7),
            (None, 20, 22),
        ]

    def test_b6_prose_says_nothing(self):
        assert loc.parse_sites({"location": "somewhere in the retry loop"}) == []
        assert loc.parse_sites({}) == []
        assert loc.parse_sites(None) == []

    def test_key_priority_is_the_measured_order_not_dict_order(self):
        # Singular keys are unambiguous by construction and win outright; a later
        # key is never merged in, so the answer cannot depend on how many
        # spellings the producer happened to use.
        meta = {"sites": ["z.py:99"], "lines": [50], "line": 7}
        assert loc.parse_sites(meta) == [(None, 7, 7)]
        assert loc.parse_sites({"sites": ["z.py:99"], "lines": [50]}) == [(None, 50, 50)]

    def test_function_is_never_a_source(self):
        assert loc.parse_sites({"function": "handle(3)"}) == []

    def test_select_site_prefers_this_file_and_refuses_a_foreign_only_value(self):
        sites = loc.parse_sites({"sites": ["other.py:3", "src/f.py:9"]})
        assert loc.select_site(sites, "f.py") == ("src/f.py", 9, 9)
        assert loc.select_site(loc.parse_sites({"sites": ["other.py:3"]}), "f.py") is None


# --- the object (Р2) ------------------------------------------------------------------


class TestCapturedObject:
    def test_shape_is_the_whole_of_r2_v2(self, conn, repo, head):
        anchor = _anchor(conn, meta={"line": 3}, project_dir=str(repo), commit=head)
        assert set(anchor) == {
            "v",
            "repo",
            "commit",
            "path",
            "line",
            "end",
            "text",
            "norm",
            "sites_dropped",
        }
        assert anchor["v"] == 2
        assert anchor["commit"] == head
        assert anchor["path"] == "f.py"
        assert anchor["line"] == 3 and anchor["end"] == 3
        assert anchor["text"] == ["def compute_the_thing(argument):"]
        assert anchor["norm"] == "v1"
        assert anchor["sites_dropped"] == 0
        assert loc.read_anchor(anchor) == (anchor, None)

    def test_text_is_verbatim_from_the_revision_not_from_the_working_tree(
        self, conn, repo, head
    ):
        # The whole reason capture reads the object store: a dirty tree would
        # otherwise give text from a revision the anchor does not name.
        (repo / "f.py").write_text("COMPLETELY DIFFERENT\n" * 10)
        anchor = _anchor(conn, meta={"line": 3}, project_dir=str(repo), commit=head)
        assert anchor["text"] == ["def compute_the_thing(argument):"]

    def test_an_abbreviated_commit_is_expanded_to_forty_hex(self, conn, repo, head):
        anchor = _anchor(conn, meta={"line": 3}, project_dir=str(repo), commit=head[:8])
        assert anchor["commit"] == head

    def test_sites_dropped_counts_the_sites_this_anchor_does_not_cover(
        self, conn, repo, head
    ):
        anchor = _anchor(
            conn, meta={"lines": [3, 4, 6]}, project_dir=str(repo), commit=head
        )
        assert anchor["line"] == 3
        assert anchor["sites_dropped"] == 2

    def test_a_span_longer_than_the_cap_is_clipped_not_refused(self, conn, repo, head):
        anchor = _anchor(conn, meta={"lines": "1-6"}, project_dir=str(repo), commit=head)
        assert anchor["line"] == 1
        assert anchor["end"] == loc.MAX_ANCHOR_LINES
        assert len(anchor["text"]) == loc.MAX_ANCHOR_LINES
        assert loc.read_anchor(anchor)[1] is None

    def test_a_span_running_past_eof_is_clipped_to_the_file(self, conn, repo, head):
        # f.py has six lines; 3-9 clips to 3-6, four lines, inside the cap.
        anchor = _anchor(conn, meta={"lines": "3-9"}, project_dir=str(repo), commit=head)
        assert (anchor["line"], anchor["end"]) == (3, 6)
        assert len(anchor["text"]) == 4
        assert loc.read_anchor(anchor)[1] is None

    def test_repo_is_the_root_commit_and_is_cached_per_process(self, conn, repo, head):
        root_commit = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
            check=True,
            capture_output=True,
        ).stdout.decode().split()[0]
        anchor = _anchor(conn, meta={"line": 3}, project_dir=str(repo), commit=head)
        assert anchor["repo"] == root_commit
        # Cached: a second capture must not pay the 8-10 ms probe again, which is
        # the whole reason the field was affordable under the write lock.
        calls = []
        real = loc._git

        def counting(root, args, budget):
            calls.append(tuple(args))
            return real(root, args, budget)

        try:
            loc._git = counting
            _anchor(
                conn,
                meta={"line": 4},
                project_dir=str(repo),
                commit=head,
                description="a second and differently worded observation of the same file",
            )
        finally:
            loc._git = real
        assert not any(a[0] == "rev-list" for a in calls), calls


class TestNormalizer:
    def test_indent_becomes_a_depth_token_and_runs_collapse(self):
        assert loc.normalize_lines(["        a   =    1"]) == [(2, "a = 1")]
        assert loc.normalize_lines(["\t\ta = 1"]) == [(2, "a = 1")]

    def test_line_endings_do_not_change_the_answer(self):
        assert loc.normalize_lines(["a = 1\r"]) == loc.normalize_lines(["a = 1"])


# --- Р8: every refusal is DATA, never a resolver error ---------------------------------


class TestRefusalTable:
    """Р8, one case per token, each asserting the same two things.

    The second assertion is the load-bearing one and the reason this class is
    not merely token coverage: `meta.resolver_errors` must be ABSENT. The
    resolver runner catches every exception a resolver raises, keeps the insert,
    and stamps that key — so a capture that let anything escape would still
    return a success-shaped row while quietly reclassifying an expected refusal
    as a broken extension.
    """

    def _refused(self, conn, **kw):
        meta = _add(conn, **kw)
        assert "resolver_errors" not in meta, meta.get("resolver_errors")
        anchor = meta["loc"]
        assert set(anchor) == {"v", "skipped", "sites_dropped"}
        assert anchor["v"] == 2
        assert anchor["skipped"] in loc.CAPTURE_REASONS
        assert loc.read_anchor(anchor) == (None, anchor["skipped"])
        return anchor["skipped"]

    def test_no_root(self, conn):
        # Nobody told capture which tree this is about. It must NOT reach for the
        # process cwd: BT-7 Р3 refuses ambient cwd, because a long-lived server's
        # cwd has nothing to do with the tracker a call writes to.
        assert self._refused(conn, meta={"line": 3}, project_dir=None) == "no_root"

    def test_no_repo(self, conn, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        # Assert the fixture really is what the test needs — a setup that quietly
        # did not hold would make this pass for the wrong reason.
        from codebugs.provenance import worktree_root

        assert worktree_root(project_dir=str(outside)) is None
        assert self._refused(conn, meta={"line": 3}, project_dir=str(outside)) == "no_repo"

    def test_no_grammar(self, conn, repo, head):
        assert (
            self._refused(
                conn, meta={"note": "somewhere in the loop"}, project_dir=str(repo), commit=head
            )
            == "no_grammar"
        )

    def test_no_matching_site(self, conn, repo, head):
        assert (
            self._refused(
                conn, meta={"sites": ["elsewhere.py:2"]}, project_dir=str(repo), commit=head
            )
            == "no_matching_site"
        )

    def test_no_commit_when_absent(self, conn, repo):
        assert (
            self._refused(conn, meta={"line": 3}, project_dir=str(repo), commit=None)
            == "no_commit"
        )

    def test_no_commit_when_the_value_is_not_a_sha(self, conn, repo):
        # Shape-checked BEFORE git is invoked, which also keeps a caller string
        # out of argv — `rev-parse` would happily accept a branch name.
        assert (
            self._refused(conn, meta={"line": 3}, project_dir=str(repo), commit="main")
            == "no_commit"
        )

    def test_commit_unreachable(self, conn, repo):
        assert (
            self._refused(conn, meta={"line": 3}, project_dir=str(repo), commit="d" * 40)
            == "commit_unreachable"
        )

    def test_path_absent_at_commit(self, conn, repo, head):
        assert (
            self._refused(
                conn, meta={"line": 3}, file="never-existed.py", project_dir=str(repo), commit=head
            )
            == "path_absent_at_commit"
        )

    def test_path_outside_the_worktree(self, conn, repo, head):
        assert (
            self._refused(
                conn, meta={"line": 3}, file="../outside.py", project_dir=str(repo), commit=head
            )
            == "path_absent_at_commit"
        )

    def test_not_a_file_is_a_real_producer_because_git_show_would_have_succeeded(
        self, conn, repo, head
    ):
        """A `file` naming a DIRECTORY (CB-88 records this as a real value).

        This is the deviation `loc.read_blob` documents, and the test is written
        to show WHY: `git show <commit>:sub` exits 0 and prints a tree listing,
        so the design's literal command would have stored a directory listing as
        the anchor's `text`. `git cat-file blob` refuses it. Both halves are
        asserted, so a change back to `git show` turns this red instead of
        silently capturing nonsense.
        """
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{head}:sub"], capture_output=True
        )
        assert shown.returncode == 0 and b"g.py" in shown.stdout
        assert (
            self._refused(conn, meta={"line": 1}, file="sub", project_dir=str(repo), commit=head)
            == "not_a_file"
        )

    def test_not_a_file_with_a_trailing_slash(self, conn, repo, head):
        assert (
            self._refused(conn, meta={"line": 1}, file="sub/", project_dir=str(repo), commit=head)
            == "not_a_file"
        )

    def test_binary(self, conn, repo, head):
        assert (
            self._refused(conn, meta={"line": 1}, file="b.bin", project_dir=str(repo), commit=head)
            == "binary"
        )

    def test_out_of_range(self, conn, repo, head):
        assert (
            self._refused(conn, meta={"line": 999}, project_dir=str(repo), commit=head)
            == "out_of_range"
        )

    def test_too_short(self, conn, repo, head):
        # `x = 1` normalizes to five characters, under MIN_ANCHOR_CHARS.
        assert (
            self._refused(conn, meta={"line": 6}, project_dir=str(repo), commit=head)
            == "too_short"
        )

    def test_too_large(self, conn, repo, head):
        assert (
            self._refused(
                conn, meta={"line": 1}, file="long.py", project_dir=str(repo), commit=head
            )
            == "too_large"
        )

    def test_timeout(self, conn, repo, head, monkeypatch):
        """An exhausted budget, through the real budget object.

        The deadline is sampled once per capture and every git call gets what is
        LEFT of it — which is what makes the guard safe: three calls at the full
        budget each would be a lock hold longer than a competing writer will
        wait, i.e. the guard authorizing the failure it exists to prevent.
        """
        monkeypatch.setattr(loc, "CAPTURE_BUDGET_S", -1.0)
        assert (
            self._refused(conn, meta={"line": 3}, project_dir=str(repo), commit=head) == "timeout"
        )

    def test_internal_error(self, conn, repo, head, monkeypatch):
        def boom(root, args, budget):
            raise OSError("git vanished mid-capture")

        monkeypatch.setattr(loc, "_git", boom)
        assert (
            self._refused(conn, meta={"line": 3}, project_dir=str(repo), commit=head)
            == "internal_error"
        )

    def test_every_capture_token_has_a_test_here(self):
        """The table is CLOSED, so the coverage claim must be mechanical.

        A token added to `CAPTURE_REASONS` without a case above turns this red
        rather than leaving the Р8 promise asserted for a set nobody re-read.
        """
        named = {
            name.removeprefix("test_")
            for name in dir(self)
            if name.startswith("test_")
        }
        tested = {token for token in loc.CAPTURE_REASONS if any(token in n for n in named)}
        assert tested == loc.CAPTURE_REASONS, loc.CAPTURE_REASONS - tested

    def test_capture_reasons_are_a_subset_of_the_closed_vocabulary(self):
        assert loc.CAPTURE_REASONS <= loc.REASONS

    def test_a_token_outside_the_closed_vocabulary_is_refused_loudly(self):
        """`_Refused` validates its own token with a raise, not an `assert`.

        `assert` is stripped under `-O`, and the optimized build would then store
        a token the closed §4.3 vocabulary does not contain — which `read_anchor`
        reports as `invalid_anchor`, blaming the stored object for a bug in this
        module. Asserted through the class rather than through a capture path,
        because no capture path can reach it while the code is correct.
        """
        with pytest.raises(ValueError, match="CAPTURE_REASONS"):
            loc._Refused("a_token_nobody_declared")

    def test_the_classifier_order_is_load_bearing(self):
        """`TimeoutExpired` IS a `SubprocessError`.

        Listing the general case first would reclassify an exhausted budget as an
        internal error and lose the one signal that says the lock was held long
        enough to matter. Asserted on the table, not on behaviour, because a
        reordered table gives a plausible-looking token either way.
        """
        kinds = [kind for kind, _ in loc._EXCEPTION_TOKENS]
        assert kinds.index(subprocess.TimeoutExpired) < kinds.index(subprocess.SubprocessError)

    def test_an_unlisted_exception_is_a_broken_resolver_and_does_stamp(
        self, conn, repo, head, monkeypatch
    ):
        """The other half of Р8, and it is what makes the table non-vacuous.

        "Everything past the table is a genuinely broken resolver, and
        `resolver_errors` is the right channel for it" is only a real statement
        if something outside the table actually reaches that channel.
        """

        def boom(root, args, budget):
            raise ZeroDivisionError("a real bug in this module")

        monkeypatch.setattr(loc, "_git", boom)
        meta = _add(conn, meta={"line": 3}, project_dir=str(repo), commit=head)
        assert "loc" not in meta
        assert [e["resolver"] for e in meta["resolver_errors"]] == ["loc.capture"]


# --- §7.3: the lock argument, both halves ---------------------------------------------


class TestCaptureBudget:
    def test_premise_a_locked_out_writer_raises_an_unclassifiable_sqlite_busy(
        self, tmp_path
    ):
        """§7.3's premise, and the whole reason the budget exists.

        `SQLITE_BUSY` is 5, which is neither in `db._is_environmental`'s
        `{8, 10, 13, 14}` nor anything a CLI arm converts — so a writer that
        waits out its `busy_timeout` behind a long capture does not get a tidy
        message, it gets a raw traceback. Pinned as a premise so a future
        classifier change turns this red instead of quietly making the budget
        pointless.

        The competitor's timeout is shortened so the premise costs 0.2 s rather
        than 5; the RELATION between the real values is the next test.
        """
        path = str(tmp_path / "t.db")
        holder = sqlite3.connect(path)
        holder.execute("PRAGMA journal_mode=WAL")
        findings.ensure_schema(holder)
        other = sqlite3.connect(path)
        other.execute("PRAGMA busy_timeout=200")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("CREATE TABLE IF NOT EXISTS probe (x)")
        try:
            with pytest.raises(sqlite3.OperationalError) as caught:
                other.execute("BEGIN IMMEDIATE")
            assert db.is_contention(caught.value)
            assert not db._is_environmental(caught.value)
        finally:
            holder.rollback()
            holder.close()
            other.close()

    def test_the_budget_is_well_under_the_writers_tolerance(self, tmp_path):
        """The RELATION, read off a real connection rather than a copied literal.

        Comparing against a hardcoded 5000 would pass forever after someone
        changed the PRAGMA, which is the "gate that cannot fire" shape.
        """
        db.init_project(str(tmp_path))
        conn = db.connect(str(tmp_path))
        try:
            busy_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        assert busy_ms > 0
        assert loc.CAPTURE_BUDGET_S * 1000 < busy_ms / 2

    def test_a_capture_holding_the_lock_does_not_break_a_competing_writer(
        self, tmp_path, repo, head, monkeypatch
    ):
        """The behavioural half: two real adds, one of them blocked mid-capture.

        The capture is held for a bounded time INSIDE the write transaction, so
        the competing writer really is queued behind it, and both must land.
        Bounded on every wait — a hang here would otherwise burn the suite's
        clock rather than fail.
        """
        db.init_project(str(tmp_path))
        entered = threading.Event()
        release = threading.Event()
        real = loc._git

        def blocking(root, args, budget):
            entered.set()
            release.wait(5.0)
            return real(root, args, budget)

        monkeypatch.setattr(loc, "_git", blocking)
        results: dict[str, object] = {}

        def slow_add():
            c = db.connect(str(tmp_path))
            try:
                results["slow"] = findings.add_finding(
                    c,
                    severity="low",
                    category="anchor_race",
                    file="f.py",
                    description="the observation whose capture is held inside the write lock",
                    meta={"line": 3},
                    reported_at_commit=head,
                    project_dir=str(repo),
                    new_category=True,
                )
            except BaseException as exc:  # noqa: BLE001 — reported through the dict
                results["slow"] = exc
            finally:
                c.close()

        def competing():
            c = db.connect(str(tmp_path))
            try:
                results["fast"] = findings.add_finding(
                    c,
                    severity="low",
                    category="anchor_race",
                    file="other.py",
                    description="an unrelated observation arriving while the lock is held",
                    new_category=True,
                )
            except BaseException as exc:  # noqa: BLE001
                results["fast"] = exc
            finally:
                c.close()

        a = threading.Thread(target=slow_add)
        a.start()
        assert entered.wait(10.0), "the capture never reached git"
        b = threading.Thread(target=competing)
        b.start()
        time.sleep(0.2)  # bounded: let B reach BEGIN IMMEDIATE and queue
        release.set()
        a.join(20.0)
        b.join(20.0)
        assert not a.is_alive() and not b.is_alive()
        for key in ("slow", "fast"):
            assert not isinstance(results[key], BaseException), results[key]
        assert results["slow"]["meta"]["loc"]["line"] == 3


# --- read side: the object's invariants ------------------------------------------------


class TestReadAnchor:
    """Р2's invariants live on the READ and never raise.

    They cannot live on the write: `updatable_keys` validates nothing (П7) and
    `restore_findings` writes meta verbatim, so a stored `loc` can be whatever a
    hand-written `meta_update` put there. A write-side validator would be a gate
    that cannot fire.
    """

    def _ok(self):
        return {
            "v": 2,
            "repo": "a" * 40,
            "commit": "b" * 40,
            "path": "src/f.py",
            "line": 3,
            "end": 4,
            "text": ["one", "two"],
            "norm": "v1",
            "sites_dropped": 0,
        }

    def test_the_tombstone_reads_as_retracted(self):
        assert loc.read_anchor(None) == (None, "retracted")

    def test_an_unknown_version_never_raises(self):
        assert loc.read_anchor({**self._ok(), "v": 7})[1] == "unsupported_anchor_version"
        assert loc.read_anchor({**self._ok(), "v": "2"})[1] == "unsupported_anchor_version"

    @pytest.mark.parametrize(
        "patch",
        [
            {"commit": "zz"},
            {"path": "/etc/passwd"},
            {"path": "../escape.py"},
            {"path": ""},
            {"line": 0},
            {"line": True},
            {"end": 2},  # end < line
            {"line": 1, "end": 1 + loc.MAX_ANCHOR_LINES},
            {"text": "one\ntwo"},
            {"text": ["only one"]},  # length disagrees with the span
            {"text": [1, 2]},
            {"text": ["x" * 3000, "y"]},
        ],
        ids=lambda p: "-".join(sorted(p)),
    )
    def test_a_broken_invariant_reads_as_invalid_anchor(self, patch):
        assert loc.read_anchor({**self._ok(), **patch})[1] == "invalid_anchor"

    def test_a_persisted_refusal_reads_back_as_its_own_token(self):
        obj = {"v": 2, "skipped": "no_grammar", "sites_dropped": 0}
        assert loc.read_anchor(obj) == (None, "no_grammar")

    def test_a_refusal_carrying_an_unknown_token_is_invalid_not_trusted(self):
        obj = {"v": 2, "skipped": "made_up", "sites_dropped": 0}
        assert loc.read_anchor(obj) == (None, "invalid_anchor")

    def test_garbage_never_raises(self):
        for value in ("string", 3, [], True):
            anchor, reason = loc.read_anchor(value)
            assert anchor is None and reason in loc.REASONS


# --- the behavioural shift this unit itself introduces ---------------------------------


class TestReservationShift:
    def test_add_now_refuses_a_caller_supplied_loc(self, conn):
        with pytest.raises(ValueError, match="reserved"):
            findings.add_finding(
                conn,
                severity="low",
                category="anchor_test",
                file="f.py",
                description="a caller inventing its own coordinates",
                meta={"loc": {"v": 2}},
                new_category=True,
            )

    def test_update_may_repair_or_retract_it(self, conn, repo, head):
        row = findings.add_finding(
            conn,
            severity="low",
            category="anchor_test",
            file="f.py",
            description="a finding whose anchor is later retracted by hand",
            meta={"line": 3},
            reported_at_commit=head,
            project_dir=str(repo),
            new_category=True,
        )
        assert row["meta"]["loc"]["line"] == 3
        # The tombstone: "do not recapture". It reads back as `retracted`, which
        # is the whole reason `loc` is declared updatable (CB-26's shape).
        updated = findings.update_finding(conn, row["id"], meta_update={"loc": None})
        assert loc.read_anchor(updated["meta"]["loc"]) == (None, "retracted")

    def test_loc_is_in_both_registry_views(self):
        assert "loc" in db.resolver_reserved_meta_keys()
        assert "loc" in db.resolver_updatable_meta_keys()

    def test_import_strips_it_because_an_import_is_not_an_observation(self, conn, repo, head):
        """A peer's anchor is coordinates in a peer's tree (CB-51 verbatim).

        Stripping is DYNAMIC — `db.resolver_reserved_meta_keys()` — so this needed
        no import-side edit, and the test exists to prove the dynamic union
        actually reaches the new key rather than to pin a list.
        """
        report = findings.import_findings(
            conn,
            [
                {
                    "id": "CB-9001",
                    "severity": "low",
                    "category": "anchor_test",
                    "file": "f.py",
                    "description": "a row exported from somebody else's tracker",
                    "meta": json.dumps({"loc": {"v": 2, "commit": "c" * 40}, "keep": 1}),
                }
            ],
        )
        assert report.imported == 1
        # The foreign id does not survive either — CB-51 mints a local one and
        # records the original as `meta.imported_id`. That is pre-existing and is
        # asserted so this test reads as a statement about `loc` and nothing else.
        rows = findings.query_findings(conn)["findings"]
        assert len(rows) == 1
        assert rows[0]["meta"] == {"keep": 1, "imported_id": "CB-9001"}


class TestRegistrations:
    """The three registrations, by the similarity/relations precedent."""

    def test_the_module_is_imported_by_the_loader(self):
        src = inspect.getsource(db._ensure_modules_loaded)
        assert "\n            loc,\n" in src

    def test_the_server_mode_slug(self):
        from codebugs import server

        assert server.SERVER_NAMES["loc"] == "codeloc"

    def test_the_cli_mode_allowlist(self):
        from codebugs import cli

        src = inspect.getsource(cli)
        start = src.index('choices=["findings"')
        assert '"loc"' in src[start : src.index("]", start)]

    def test_the_read_side_registers_the_mcp_provider(self):
        """Т-a pinned that `loc` registered NEITHER provider, as a scope
        statement — `SERVER_NAMES` gaining an entry does not create a tool, so
        the golden could not move. Т-b is the unit that adds both, so the pin is
        INVERTED rather than deleted: a registration nothing asserts is a
        registration a refactor can drop silently, and the wire golden would
        then move in the quiet direction.
        """
        assert [p.name for p in db.get_tool_providers(mode="loc")] == ["loc"]

    def test_the_two_anchor_tools_reach_the_wire(self):
        """Named explicitly, because the golden is a whole-file snapshot and a
        reader of a 160-line diff should not have to infer which two tools it is
        about."""
        golden = json.loads(
            (pathlib.Path(__file__).parent / "golden" / "mcp_schema.json").read_text()
        )
        names = {t["name"] for t in golden}
        assert {"anchor_resolve", "anchor_recapture"} <= names

    def test_the_read_side_registers_the_cli_provider(self):
        """Т-a deliberately registered NEITHER provider and pinned that as a
        scope statement; Т-b is the unit that adds them, so the pin is inverted
        rather than deleted — a registration nothing asserts is a registration
        that can vanish in a refactor.
        """
        assert [p.name for p in db.get_cli_providers(mode="loc")] == ["loc"]


class TestZeroSql:
    def test_the_module_issues_no_sql(self):
        """The licence for a non-domain module to exist at all (similarity's rule).

        By AST rather than by grepping for SQL keywords: the first draft did the
        latter and matched the word `SELECT` inside a COMMENT, which is this
        repository's own recorded lesson about `TestWriteCallSitesRatchet`. What
        the rule actually forbids is EXECUTION, and that is a call node.
        """
        tree = ast.parse(inspect.getsource(loc))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & {"execute", "executemany", "executescript", "commit"}, called


class TestGrammarCeilingIsHonest:
    def test_a_row_that_says_nothing_still_records_that_it_said_nothing(self, conn, repo, head):
        """Р7: the refusal is DATA.

        63.6% of this tracker's rows and 79.2% of autosorter's name no span at
        all, and that ceiling is the headline number of the whole design. It is
        only countable because `no_grammar` is stored rather than omitted —
        `query(meta_key="loc")` reaches every row, refusals included.
        """
        _add(conn, meta=None, project_dir=str(repo), commit=head)
        rows = findings.query_findings(conn, meta_key="loc")["findings"]
        assert len(rows) == 1
        assert rows[0]["meta"]["loc"]["skipped"] == "no_grammar"


def test_module_has_no_ambient_cwd_fallback():
    """BT-7 Р3 in capitals: the root is never taken from the process cwd.

    Structural, because the behavioural version cannot discriminate: a test
    running inside this repository would find a perfectly good worktree root at
    the cwd and pass either way.
    """
    src = inspect.getsource(loc)
    assert "os.getcwd" not in src
    assert "worktree_root(project_dir=project_dir)" in src
    assert "worktree_root()" not in src


def test_the_capture_resolver_is_registered_with_both_key_declarations():
    registered = {r.name: r for r in db._pre_add_resolvers}
    assert "loc.capture" in registered, sorted(registered)
    entry = registered["loc.capture"]
    assert entry.meta_keys == frozenset({"loc"})
    assert entry.updatable_keys == frozenset({"loc"})


# ======================================================================================
# BT-7 Т-b — the READ side: resolving a stored anchor to a line on HEAD.
#
# The set is deliberately two-sided. A resolver that answers `unknown` to
# everything would pass every refusal test here, so the CONTROLS — an ordinary
# live line resolving `current` through channel `git`, and a line pushed down by
# an edit above it resolving `moved` with the NEW number — are what make the
# refusal tests mean anything.
# ======================================================================================


def _rev(root, ref="HEAD"):
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", ref], check=True, capture_output=True
    )
    return out.stdout.decode().strip()


def _commit(root, msg="c"):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    return _rev(root)


def _new_repo(tmp_path, name="r"):
    root = tmp_path / name
    root.mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    return root


#: Twelve lines that are individually long enough to clear MIN_ANCHOR_CHARS and
#: distinct enough that a wrong line number is visible in a failure message.
def _body(prefix="ALPHA", n=12):
    return "".join(f"{prefix}_VALUE_{i:02d} = compute_the_thing({i})\n" for i in range(1, n + 1))


def _span_anchor(root, commit, path, line, end, **over):
    """A well-formed anchor over `<commit>:<path>` lines `line..end`, built from git."""
    blob = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    lines = blob.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    obj = {
        "v": 2,
        "repo": loc.repo_identity(str(root)),
        "commit": commit,
        "path": path,
        "line": line,
        "end": end,
        "text": lines[line - 1 : end],
        "norm": "v1",
        "sites_dropped": 0,
    }
    obj.update(over)
    return obj


def _resolve(root, anchor):
    return loc.resolve_anchor(
        str(root), anchor, head=_rev(root), repo=loc.repo_identity(str(root))
    )


class TestAncestorGate:
    """Step 0 of Р5, and the ONLY finding in the structural attack that breaks the
    ORDINARY case rather than a rare one: in this repository every card is filed
    on an unmerged branch."""

    def test_a_card_filed_on_an_unmerged_branch_is_not_reported_lost(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        _commit(root, "init")
        # A REAL unmerged branch, not an imitation of one: the premise being
        # guarded is a property of `blame --reverse` over a range whose left end
        # is off the first-parent line, and a fabricated SHA would not exercise it.
        _git(root, "checkout", "-q", "-b", "feat")
        (root / "side.py").write_text("SIDE = 1\n")
        branch_commit = _commit(root, "branch work")
        _git(root, "checkout", "-q", "main")
        (root / "other.py").write_text("OTHER = 1\n")
        _commit(root, "main moves on")

        anchor = _span_anchor(root, branch_commit, "mod.py", 4, 6)
        # The premise itself, pinned: without the gate this is a SILENT false
        # "lost" — rc 0, empty stderr, and an attribution that is not HEAD.
        proc = subprocess.run(
            ["git", "-C", str(root), "blame", "--reverse", "-p", "-L", "4,6",
             f"{branch_commit}..HEAD", "--", "mod.py"],
            capture_output=True,
        )
        assert proc.returncode == 0 and proc.stderr == b""
        assert _rev(root) not in proc.stdout.decode()

        got = _resolve(root, anchor)
        assert got["status"] == "unknown"
        assert got["reason"] == "commit_not_ancestor"
        assert got["channel"] is None

    def test_the_same_anchor_resolves_once_the_branch_is_merged(self, tmp_path):
        """The state is TEMPORARY, which is the other half of the design's claim.

        Without this, `commit_not_ancestor` could be implemented as a permanent
        refusal and every test above would still pass.
        """
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        _commit(root, "init")
        _git(root, "checkout", "-q", "-b", "feat")
        (root / "side.py").write_text("SIDE = 1\n")
        branch_commit = _commit(root, "branch work")
        _git(root, "checkout", "-q", "main")
        _git(root, "merge", "--no-ff", "-q", "-m", "merge feat", "feat")

        got = _resolve(root, _span_anchor(root, branch_commit, "mod.py", 4, 6))
        assert got["status"] == "current"
        assert got["channel"] == "git"


class TestChannelAControls:
    """The set would be one-sided without these: an implementation answering
    `unknown` to everything passes every refusal test and fails only here."""

    def test_an_ordinary_live_line_is_current_through_git(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "unrelated.py").write_text("U = 1\n")
        _commit(root, "unrelated")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 6))
        assert got["status"] == "current"
        assert got["channel"] == "git"
        assert (got["line"], got["end"]) == (4, 6)
        assert got["path"] == "mod.py"
        assert got["survived"] == "3/3"
        assert got["resolved_against"]["head"] == _rev(root)

    def test_a_line_pushed_down_by_an_edit_above_it_is_moved_with_the_new_number(
        self, tmp_path
    ):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "mod.py").write_text("PREFIX_A = 1\nPREFIX_B = 2\n" + _body())
        _commit(root, "two lines inserted at the top")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 6))
        assert got["status"] == "moved"
        assert got["channel"] == "git"
        # The whole point of the assertion: the NEW coordinate, not the old one.
        # A parser that transposed porcelain's two line numbers under `--reverse`
        # returns 4 here, and every fixture that does not shift its lines is blind
        # to that, because the two numbers are equal when nothing moved.
        assert (got["line"], got["end"]) == (6, 8)

    def test_the_resolved_coordinate_really_holds_the_anchored_text(self, tmp_path):
        """A coordinate nobody dereferences is not evidence that it is right."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "mod.py").write_text("PREFIX = 1\n" + _body())
        _commit(root, "shift by one")

        anchor = _span_anchor(root, first, "mod.py", 4, 6)
        got = _resolve(root, anchor)
        at_head = (root / "mod.py").read_text().split("\n")
        assert at_head[got["line"] - 1 : got["end"]] == anchor["text"]


class TestMovedFile:
    """`moved_file` is a SEPARATE status, not `moved` with a different path: a
    consumer must SEE that the code left the file it asked about, rather than
    receive a line number in a file it never named."""

    def test_code_lifted_into_another_file_resolves_there_with_moved_file(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        # A span of FIVE. The design measured that a controlled split is not
        # tracked on any span of three or fewer, so a two-line fixture would be
        # green against a broken `-C -C` and prove nothing.
        lines = _body().split("\n")
        moved_out = lines[3:8]
        (root / "mod.py").write_text("\n".join(lines[:3] + lines[8:]))
        (root / "split_out.py").write_text("\n".join(moved_out) + "\n")
        _commit(root, "lift five lines into split_out.py")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 8))
        assert got["status"] == "moved_file"
        assert got["path"] == "split_out.py"
        assert got["channel"] == "git"
        assert got["survived"] == "5/5"

    def test_a_plain_rename_also_resolves_to_the_new_path(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        _git(root, "mv", "mod.py", "renamed.py")
        _commit(root, "rename")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 8))
        assert got["status"] == "moved_file"
        assert got["path"] == "renamed.py"


class TestVerify:
    """Р5 step 2, three outcomes SEPARATELY. The verify is not skippable, and it
    reads the RESOLVED path — reading the RECORDED one is what produced the
    design's phantom 'git is wrong 0.21% of the time', because it declares every
    successful move a failure."""

    def test_every_surviving_line_agreeing_yields_the_answer_and_the_ratio(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "unrelated.py").write_text("U = 1\n")
        _commit(root, "unrelated")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 3, 6))
        assert got["status"] == "current"
        assert got["survived"] == "4/4"

    def test_a_partially_surviving_span_answers_and_says_so(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        lines = _body().split("\n")
        del lines[4]  # kill exactly one of the two anchored lines
        (root / "mod.py").write_text("\n".join(lines))
        _commit(root, "delete one anchored line")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 5))
        assert got["status"] in ("current", "moved")
        assert got["survived"] == "1/2"

    def test_no_surviving_line_is_lost(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        lines = _body().split("\n")
        del lines[3:6]
        (root / "mod.py").write_text("\n".join(lines))
        _commit(root, "delete the anchored span")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 6))
        assert got["status"] == "lost"
        assert got["survived"] == "0/3"
        assert got["channel"] == "git"

    def test_a_surviving_line_whose_text_disagrees_is_verify_mismatch(self, tmp_path):
        """Reachable because `updatable_keys` validates nothing: a hand-written
        anchor can name a real span and carry text that was never there."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "unrelated.py").write_text("U = 1\n")
        _commit(root, "unrelated")

        anchor = _span_anchor(root, first, "mod.py", 4, 6)
        anchor["text"] = [anchor["text"][0], "THIS_LINE_WAS_NEVER_THERE = 0", anchor["text"][2]]
        got = _resolve(root, anchor)
        assert got["status"] == "unknown"
        assert got["reason"] == "verify_mismatch"
        assert got["channel"] == "git"

    def test_the_verify_reads_the_resolved_path_not_the_recorded_one(self, tmp_path):
        """The single detail the design says was wrong in its own measuring rig.

        After a move the RECORDED path may not even exist at HEAD; a verify that
        reads it declares the move a failure. This asserts the successful answer,
        and additionally that the recorded path really is gone — otherwise the
        test could pass by accident on a repository where both files exist.
        """
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        _git(root, "mv", "mod.py", "renamed.py")
        _commit(root, "rename")

        assert not (root / "mod.py").exists()
        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 8))
        assert got["status"] == "moved_file"
        assert got["reason"] is None


class TestNonAsciiPaths:
    """`core.quotePath=false`. Porcelain C-quotes a non-ASCII path by DEFAULT,
    and the path is what decides `moved_file` — so the default reports a move for
    a file that never moved. This repository has paid for that same default three
    times elsewhere (`_guard_conflict_markers`, the plan-note allowlist, the
    commit-msg gate)."""

    def test_a_file_with_a_non_ascii_name_stays_current(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "модуль.py").write_text(_body())
        first = _commit(root, "init")
        (root / "unrelated.py").write_text("U = 1\n")
        _commit(root, "unrelated")

        # The premise, so the mutation probe on this flag is not vacuous: with
        # the default, git really does quote the name in porcelain output.
        quoted = subprocess.run(
            ["git", "-C", str(root), "blame", "--reverse", "-p", "-L", "4,6",
             f"{first}..HEAD", "--", "модуль.py"],
            capture_output=True,
        ).stdout.decode()
        assert 'filename "' in quoted, "premise gone: git no longer quotes by default"

        got = _resolve(root, _span_anchor(root, first, "модуль.py", 4, 6))
        assert got["status"] == "current"
        assert got["path"] == "модуль.py"
        # ASSERTING THE CHANNEL IS THE WHOLE TEST, and the first draft did not.
        # Found by the mutation probe, not by review: with the flag removed,
        # channel A receives a C-quoted path, fails its verify — correctly — and
        # then CHANNEL B SILENTLY RESCUES THE ANSWER with an identical `current`.
        # A test reading only the status therefore cannot see a broken channel A
        # at all. The two channels agreeing is the design's own measured property
        # (133 of 133), which is exactly what makes the fallback a mask for this
        # class of defect; every channel-A test in this file asserts its channel
        # for that reason.
        assert got["channel"] == "git"
        assert got["survived"] == "3/3"


class TestChannelB:
    """Secondary, and NOT redundant: it is the only thing that recovers a line
    deleted and later restored byte-for-byte, which the core reads as lost
    because the liveness test runs before the verify and can only reject."""

    def test_it_answers_where_channel_a_cannot_and_the_channel_says_so(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        lines = _body().split("\n")
        kept = lines[:3] + lines[6:]
        (root / "mod.py").write_text("\n".join(kept))
        _commit(root, "delete the span")
        (root / "mod.py").write_text(_body())  # restored byte for byte
        _commit(root, "revert the deletion")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 6))
        assert got["status"] == "current"
        assert got["channel"] == "content", "channel A must not be able to answer here"
        assert (got["line"], got["end"]) == (4, 6)

    def test_channel_b_is_refused_below_the_noise_floor(self, tmp_path):
        """`MIN_ANCHOR_CHARS` is a noise floor, and below it a normalized span
        matches almost anywhere — which would make a wrong answer this channel's
        NORMAL output rather than its failure mode."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text("x = 1\ny = 2\nz = 3\nw = 4\n" * 4)
        first = _commit(root, "init")
        (root / "mod.py").write_text("A = 9\n" + "x = 1\ny = 2\nz = 3\nw = 4\n" * 4)
        _commit(root, "shift")

        anchor = _span_anchor(root, first, "mod.py", 2, 2)
        assert loc._anchor_chars(anchor["text"]) < loc.MIN_ANCHOR_CHARS
        got = _resolve(root, anchor)
        assert got["channel"] != "content"


class TestResolutionRefusals:
    def test_an_anchor_from_another_tree_is_repo_mismatch(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")

        got = _resolve(root, _span_anchor(root, first, "mod.py", 4, 6, repo="0" * 40))
        assert got["status"] == "unknown"
        assert got["reason"] == "repo_mismatch"
        assert got["channel"] is None

    def test_an_anchor_carrying_no_repo_at_all_fails_closed(self, tmp_path):
        """Reachable: `read_anchor` does not require the field, and
        `updatable_keys` validates nothing. Fail CLOSED — the field's only job is
        to prevent a confidently wrong answer, so 'cannot check' is a refusal."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        anchor = _span_anchor(root, first, "mod.py", 4, 6)
        del anchor["repo"]

        got = _resolve(root, anchor)
        assert got["reason"] == "repo_mismatch"

    def test_a_commit_this_tree_does_not_have_is_commit_unreachable(self, tmp_path):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        anchor = {**_span_anchor(root, first, "mod.py", 4, 6), "commit": "0" * 40}

        got = _resolve(root, anchor)
        assert got["status"] == "unknown"
        assert got["reason"] == "commit_unreachable"


class TestBatchSurface:
    """`resolve_findings` — and the SUMMARY, which is a ratified demand counter
    (the owner reads the `moved_file` frequency out of it), not a convenience."""

    def _tracker(self, tmp_path, conn, *, anchors):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "unrelated.py").write_text("U = 1\n")
        _commit(root, "unrelated")
        ids = []
        for spec in anchors:
            fid = findings.add_finding(
                conn,
                severity="low",
                category="anchor_batch",
                file="mod.py",
                description=f"batch row {len(ids)} with a description long enough to be unique",
                new_category=True,
            )["id"]
            value = spec if not callable(spec) else spec(root, first)
            findings.update_finding(conn, fid, meta_update={"loc": value})
            ids.append(fid)
        return root, first, ids

    def test_the_summary_denominator_is_anchored_rows_not_every_finding(
        self, tmp_path, conn
    ):
        """The number goes to the owner, so it carries its predicate (К-5б).

        Two findings with an anchor, two without one at all. `summary` must sum
        to `anchored` — not to `total` — or the `moved_file` share is computed
        against a population it does not describe.
        """
        root, first, _ids = self._tracker(
            tmp_path,
            conn,
            anchors=[
                lambda r, c: _span_anchor(r, c, "mod.py", 4, 6),
                lambda r, c: _span_anchor(r, c, "mod.py", 7, 9),
            ],
        )
        for i in range(2):
            findings.add_finding(
                conn,
                severity="low",
                category="anchor_batch",
                file="mod.py",
                description=f"row without any anchor at all number {i}",
                annotate=False,  # no resolver runs, so no `loc` key is stamped
            )

        out = loc.resolve_findings(conn, category="anchor_batch", project_dir=str(root))
        assert out["total"] == 4
        assert out["anchored"] == 2
        assert out["without_anchor"] == 2
        assert len(out["results"]) == 2
        assert sum(out["summary"].values()) == out["anchored"]
        # Every status present with a zero, for the same reason each record's
        # keys are unconditional: a missing bucket reads as "not evaluated".
        assert set(out["summary"]) == set(loc.STATUSES)
        assert out["summary"]["current"] == 2

    def test_a_persisted_refusal_and_the_tombstone_are_anchored_rows(self, tmp_path, conn):
        """They CARRY an anchor — that is what makes the lost-rate countable at
        all (Р7). They land in `unknown` with the stored token."""
        root, _first, _ids = self._tracker(
            tmp_path,
            conn,
            anchors=[{"v": 2, "skipped": "no_grammar", "sites_dropped": 0}, None],
        )
        out = loc.resolve_findings(conn, category="anchor_batch", project_dir=str(root))
        assert out["anchored"] == 2
        assert out["summary"]["unknown"] == 2
        reasons = sorted(r["anchor"]["reason"] for r in out["results"])
        assert reasons == ["no_grammar", "retracted"]

    def test_the_moved_file_count_is_readable_from_the_summary(self, tmp_path, conn):
        """The sixth ratification in one assertion: the frequency of `moved_file`
        in live data is what the owner turns into arithmetic."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        lines = _body().split("\n")
        (root / "mod.py").write_text("\n".join(lines[:3] + lines[8:]))
        (root / "split_out.py").write_text("\n".join(lines[3:8]) + "\n")
        _commit(root, "lift")

        fid = findings.add_finding(
            conn,
            severity="low",
            category="anchor_batch",
            file="mod.py",
            description="a row whose code was lifted into another file entirely",
            new_category=True,
        )["id"]
        findings.update_finding(
            conn, fid, meta_update={"loc": _span_anchor(root, first, "mod.py", 4, 8)}
        )

        out = loc.resolve_findings(conn, category="anchor_batch", project_dir=str(root))
        assert out["summary"]["moved_file"] == 1
        assert out["anchored"] == 1

    def test_an_absent_project_dir_refuses_rather_than_reading_an_ambient_tree(
        self, tmp_path, conn
    ):
        """BT-7 Р3 refuses ambient cwd IN CAPITALS: resolving against whatever
        tree the process stands in is the confidently-wrong answer."""
        root, _first, _ids = self._tracker(
            tmp_path, conn, anchors=[lambda r, c: _span_anchor(r, c, "mod.py", 4, 6)]
        )
        out = loc.resolve_findings(conn, category="anchor_batch", project_dir=None)
        assert out["anchored"] == 1
        assert out["results"][0]["anchor"]["reason"] == "no_root"

    def test_the_all_sentinel_is_type_pinned(self, tmp_path, conn):
        """A bare `status == "all"` is satisfied by `unittest.mock.ANY`, which
        compares equal to everything (CB-25's trap, and group_report's fix)."""
        from unittest import mock

        root, _first, _ids = self._tracker(
            tmp_path, conn, anchors=[lambda r, c: _span_anchor(r, c, "mod.py", 4, 6)]
        )
        with pytest.raises(ValueError):
            loc.resolve_findings(conn, status=mock.ANY, project_dir=str(root))


class TestReadSideInvariants:
    """Р2's invariants are checked ON THE READ and a violation is `unknown(...)`,
    NEVER an exception — because `restore_findings` stores meta verbatim and
    `updatable_keys` validates nothing (§10 п.5), so a hand-assembled `loc` is a
    reachable state, not a hypothetical."""

    def _row(self, conn, value):
        fid = findings.add_finding(
            conn,
            severity="low",
            category="anchor_inv",
            file="mod.py",
            description="a row whose stored anchor is assembled by hand for this test",
            new_category=True,
        )["id"]
        findings.update_finding(conn, fid, meta_update={"loc": value})
        return fid

    @pytest.mark.parametrize(
        ("value", "reason"),
        [
            ({"v": 2, "commit": "zz", "path": "a.py", "line": 1, "end": 1, "text": ["x"]},
             "invalid_anchor"),
            ({"v": 2, "commit": "a" * 40, "path": "../out.py", "line": 1, "end": 1,
              "text": ["x"]}, "invalid_anchor"),
            ("not even an object", "invalid_anchor"),
            ({"v": 99, "commit": "a" * 40, "path": "a.py", "line": 1, "end": 1,
              "text": ["x"]}, "unsupported_anchor_version"),
            (None, "retracted"),
        ],
    )
    def test_garbage_written_through_meta_update_degrades_and_never_raises(
        self, tmp_path, conn, value, reason
    ):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        _commit(root, "init")
        self._row(conn, value)

        out = loc.resolve_findings(conn, category="anchor_inv", project_dir=str(root))
        assert out["anchored"] == 1
        assert out["results"][0]["anchor"]["status"] == "unknown"
        assert out["results"][0]["anchor"]["reason"] == reason

    def test_a_row_whose_meta_column_does_not_parse_at_all_is_not_an_abort(
        self, tmp_path, conn
    ):
        """One malformed column must not take a ten-thousand-row batch with it."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        good = self._row(conn, _span_anchor(root, first, "mod.py", 4, 6))
        bad = findings.add_finding(
            conn,
            severity="low",
            category="anchor_inv",
            file="mod.py",
            description="a row whose stored meta column will be corrupted directly",
            annotate=False,
        )["id"]
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", ("{not json", bad))

        out = loc.resolve_findings(conn, category="anchor_inv", project_dir=str(root))
        assert out["total"] == 2
        assert out["anchored"] == 1
        assert out["results"][0]["finding_id"] == good


class TestRecaptureFourPoints:
    """§9 requires `anchor_recapture` to be SPECIFIED in four points, and
    'specified' means: behaviour chosen, written into the docstring, and pinned
    by a test. One test per point."""

    def _tracked(self, tmp_path, conn, *, meta=None):
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        fid = findings.add_finding(
            conn,
            severity="low",
            category="anchor_recap",
            file="mod.py",
            description="a row that the repair verb will rebuild the anchor of",
            meta=meta if meta is not None else {"line": "4-6"},
            reported_at_commit=first,
            new_category=True,
        )["id"]
        return root, first, fid

    def _stored(self, conn, fid):
        return findings.get_finding(conn, fid)["meta"].get("loc")

    # --- point 1: what runs inside a transaction -------------------------------------
    def test_point1_the_git_capture_runs_with_no_transaction_open(self, tmp_path, conn):
        """The split IS the answer: scan and capture outside, check-and-write
        inside. Holding the lock across a batch of captures would exceed a
        competing writer's `busy_timeout` after three rows, and `SQLITE_BUSY` is
        not classified by `db._is_environmental` — that writer gets a raw
        traceback, which §7.3 names as the cost to avoid."""
        root, _first, fid = self._tracked(tmp_path, conn)
        seen = []
        real = loc.capture

        def spy(observation):
            seen.append(conn.in_transaction)
            return real(observation)

        loc.capture = spy
        try:
            loc.recapture_findings(
                conn, finding_id=fid, project_dir=str(root), apply=True
            )
        finally:
            loc.capture = real
        assert seen and not any(seen), "capture must not run under the write lock"

    def test_point1_the_check_and_the_write_share_one_transaction(self, tmp_path, conn):
        """Structural, because behaviour cannot distinguish a CAS from a
        check-then-act without real concurrency: the re-read and the write are
        both inside one `db.txn`, which takes the write lock BEFORE the read."""
        src = inspect.getsource(loc._apply_recapture)
        body = src[src.index("with db.txn("):]
        assert "anchor_candidates" in body
        assert "update_finding" in body

    # --- point 2: a failure never replaces a valid anchor -----------------------------
    def test_point2_a_failed_capture_does_not_replace_a_valid_stored_anchor(
        self, tmp_path, conn
    ):
        """The one point the design answers itself, and the one that must not be
        got wrong: the refusal is usually about the ENVIRONMENT, while the anchor
        it would destroy is still perfectly good in a clone that has the history."""
        root, first, fid = self._tracked(tmp_path, conn)
        good = _span_anchor(root, first, "mod.py", 4, 6)
        findings.update_finding(conn, fid, meta_update={"loc": good})

        # `project_dir=None` makes every capture refuse with `no_root` — an
        # environmental refusal, exactly the shape this point exists for.
        out = loc.recapture_findings(conn, finding_id=fid, project_dir=None, apply=True)
        assert out["summary"]["kept"] == 1
        assert out["results"][0]["outcome"] == "kept"
        assert self._stored(conn, fid) == good, "the valid anchor was destroyed"

    def test_point2_a_failed_capture_may_replace_a_stored_refusal(self, tmp_path, conn):
        """The other side of the same rule: `kept` protects COORDINATES, not any
        stored value. Without this, point 2 could be implemented as 'never write
        on a refusal', which would freeze every refusal token forever."""
        root, _first, fid = self._tracked(tmp_path, conn)
        findings.update_finding(
            conn, fid, meta_update={"loc": {"v": 2, "skipped": "no_grammar",
                                            "sites_dropped": 0}}
        )
        out = loc.recapture_findings(conn, finding_id=fid, project_dir=None, apply=True)
        assert out["results"][0]["outcome"] in ("updated", "unchanged")
        assert self._stored(conn, fid)["skipped"] == "no_root"

    # --- point 3: the tombstone --------------------------------------------------------
    def test_point3_the_tombstone_is_left_alone_by_default(self, tmp_path, conn):
        """`loc: null` means 'do not recapture' and is written BY HAND for that
        purpose; a bulk repair sweeping it away silently would make the tombstone
        unwritable in practice."""
        root, _first, fid = self._tracked(tmp_path, conn)
        findings.update_finding(conn, fid, meta_update={"loc": None})

        out = loc.recapture_findings(conn, finding_id=fid, project_dir=str(root), apply=True)
        assert out["results"][0]["outcome"] == "tombstoned"
        assert self._stored(conn, fid) is None

    def test_point3_an_explicit_flag_overrides_it(self, tmp_path, conn):
        root, _first, fid = self._tracked(tmp_path, conn)
        findings.update_finding(conn, fid, meta_update={"loc": None})

        out = loc.recapture_findings(
            conn, finding_id=fid, project_dir=str(root), apply=True, force_tombstone=True
        )
        assert out["results"][0]["outcome"] == "updated"
        assert self._stored(conn, fid)["line"] == 4

    # --- point 4: the version check ---------------------------------------------------
    def test_point4_a_row_whose_anchor_moved_under_the_capture_is_left_alone(
        self, tmp_path, conn
    ):
        """Single-threaded and therefore not timing-dependent: the competing
        write is injected DURING the capture, which is exactly the window point 1
        deliberately leaves outside the lock."""
        root, first, fid = self._tracked(tmp_path, conn)
        real = loc.capture

        def racing(observation):
            out = real(observation)
            findings.update_finding(
                conn, fid, meta_update={"loc": _span_anchor(root, first, "mod.py", 7, 9)}
            )
            return out

        loc.capture = racing
        try:
            out = loc.recapture_findings(
                conn, finding_id=fid, project_dir=str(root), apply=True
            )
        finally:
            loc.capture = real
        assert out["results"][0]["outcome"] == "stale"
        # The OTHER writer's value survived — that is the whole point.
        assert self._stored(conn, fid)["line"] == 7

    def test_point4_compares_the_anchor_not_a_row_timestamp(self, tmp_path, conn):
        """An unrelated write in the same window must not cost a repair."""
        root, _first, fid = self._tracked(tmp_path, conn)
        real = loc.capture

        def racing(observation):
            out = real(observation)
            findings.update_finding(conn, fid, append_note="an unrelated note")
            return out

        loc.capture = racing
        try:
            out = loc.recapture_findings(
                conn, finding_id=fid, project_dir=str(root), apply=True
            )
        finally:
            loc.capture = real
        assert out["results"][0]["outcome"] == "updated"

    # --- the verb's own shape ----------------------------------------------------------
    def test_it_is_a_dry_run_by_default(self, tmp_path, conn):
        root, _first, fid = self._tracked(tmp_path, conn)
        before = self._stored(conn, fid)
        out = loc.recapture_findings(conn, finding_id=fid, project_dir=str(root))
        assert out["applied"] is False
        assert out["results"][0]["outcome"] == "would_update"
        assert self._stored(conn, fid) == before

    def test_a_repaired_anchor_is_what_capture_would_have_produced(self, tmp_path, conn):
        """The repair BUILDS the object — Р4's whole point is that a human typing
        JSON is not the sanctioned path — so it must be byte-identical to the
        file-time one, not a second construction of the same shape."""
        root, first, fid = self._tracked(tmp_path, conn)
        findings.update_finding(conn, fid, meta_update={"loc": {"v": 2, "skipped": "timeout",
                                                               "sites_dropped": 0}})
        loc.recapture_findings(conn, finding_id=fid, project_dir=str(root), apply=True)
        assert self._stored(conn, fid) == loc.capture(
            {
                "file": "mod.py",
                "meta": {"line": "4-6"},
                "reported_at_commit": first,
                "project_dir": str(root),
            }
        )


class TestReadSideRegistrations:
    def test_the_cli_verbs_are_registered(self):
        providers = {p.name for p in db.get_cli_providers()}
        assert "loc" in providers
        parser = _built_cli_parser()
        assert "anchor-resolve" in parser
        assert "anchor-recapture" in parser

    def test_the_module_still_issues_no_sql(self):
        """Restated for the read side because that is where the temptation is:
        resolution reads finding ROWS, and the accessor for them lives in
        `findings.py` by the `similarity_candidates` precedent. A single innocent
        SELECT here would revoke the module's licence to exist."""
        tree = ast.parse(inspect.getsource(loc))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & {"execute", "executemany", "executescript", "commit"}, called


def _built_cli_parser():
    """The subcommand names the CLI actually exposes, built through the real
    registry rather than by reading source."""
    import argparse

    from codebugs import db as _db

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    commands = {}
    for provider in _db.get_cli_providers():
        provider.register_fn(sub, commands)
    return set(commands)


class TestReadSideResiduals:
    """Three defects the end-to-end read of this unit's own artifact found, each
    of which every test above was blind to. They are pinned rather than merely
    fixed, because all three are the shape where a wrong answer looks like a
    right one."""

    def test_a_blame_that_could_not_run_is_not_reported_as_lost(self, tmp_path):
        """A path that did not exist at the anchored revision makes `blame` exit
        non-zero. Reporting that as `lost` is a claim about the CODE derived from
        a failure to LOOK — the same "guard reporting clean because it could not
        look" shape this repository has recorded three times."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        (root / "later.py").write_text(_body("BETA"))
        _commit(root, "a file that did not exist at `first`")

        anchor = _span_anchor(root, _rev(root), "later.py", 4, 6)
        anchor["commit"] = first  # coordinates claimed for a revision without it
        # Channel B must ALSO be unable to answer, or it legitimately rescues the
        # call and the classifier under test never runs. (It did on the first
        # draft of this test — the recorded path exists at HEAD and held the
        # text, so B answered `current`, which is the correct behaviour and made
        # the fixture, not the code, wrong.)
        anchor["text"] = [
            "THIS_TEXT_IS_NOWHERE_IN_THE_TREE_AT_ALL = 1",
            "NEITHER_IS_THIS_SECOND_LINE_OF_IT = 2",
            "NOR_THIS_THIRD_ONE = 3",
        ]

        got = _resolve(root, anchor)
        assert got["status"] == "unknown"
        assert got["reason"] == "path_absent_at_commit"
        assert got["status"] != "lost"

    def test_a_forced_tombstone_is_not_destroyed_by_a_failed_recapture(
        self, tmp_path, conn
    ):
        """`force_tombstone` says "recapture this one", NOT "destroy the
        instruction if the recapture fails". Writing a refusal object over
        `loc: null` is strictly worse than leaving it: the tombstone means
        "never recapture", while a refusal object is something the very next
        UNFORCED run would happily overwrite — so the failure would quietly undo
        a deliberate decision one run later."""
        root = _new_repo(tmp_path)
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        fid = findings.add_finding(
            conn,
            severity="low",
            category="anchor_recap",
            file="mod.py",
            description="a row whose tombstone must survive a failed forced recapture",
            meta={"line": "4-6"},
            reported_at_commit=first,
            new_category=True,
        )["id"]
        findings.update_finding(conn, fid, meta_update={"loc": None})

        out = loc.recapture_findings(
            conn, finding_id=fid, project_dir=None, apply=True, force_tombstone=True
        )
        assert out["results"][0]["outcome"] == "kept"
        assert findings.get_finding(conn, fid)["meta"]["loc"] is None

    def test_the_cli_verbs_actually_render(self, tmp_path, monkeypatch, capsys):
        """`fmt.format_table` takes a list of DICTS keyed by column name, and the
        first draft of both handlers passed lists of values — an AttributeError
        on the first row. No domain test could see it: the handlers are closures
        registered into the parser, so only running the verb executes them."""
        import sys as _sys

        from codebugs import cli

        project = tmp_path / "proj"
        project.mkdir()
        project = str(project)
        db.init_project(project)
        root = _new_repo(tmp_path, name="proj_repo")
        (root / "mod.py").write_text(_body())
        first = _commit(root, "init")
        c = db.connect(project)
        fid = findings.add_finding(
            c,
            severity="low",
            category="anchor_cli",
            file="mod.py",
            description="a row rendered by the CLI verb in this test",
            new_category=True,
        )["id"]
        findings.update_finding(
            c, fid, meta_update={"loc": _span_anchor(root, first, "mod.py", 4, 6)}
        )
        c.close()

        for argv in (
            ["codebugs", "--tracker-root", project, "anchor-resolve",
             "--status", "all", "--repo", str(root)],
            ["codebugs", "--tracker-root", project, "anchor-recapture",
             "--status", "all", "--repo", str(root)],
        ):
            monkeypatch.setattr(_sys, "argv", argv)
            cli.main()
        out = capsys.readouterr().out
        assert fid in out
        assert "current" in out
        # The denominator travels WITH the number, never left to the reader.
        assert "carry an anchor" in out
        assert "Dry run" in out


class TestBlameInvocation:
    """The blame argv, pinned STRUCTURALLY — and the reason is a measurement, not
    a preference.

    NO FIXTURE DISCRIMINATES `-C -C` FROM `-C` UNDER `--reverse`, and that is a
    MEASURED RESULT rather than a gap in this file. Both flags were run against
    a controlled split and four variants of it (two-commit split; split then
    tracked rename; split then untracked copy+delete; destination created in a
    commit that leaves the source untouched) and the output was BYTE-IDENTICAL
    every time.

    The structural reason, and it is specific to the direction of travel:
    `-C -C`'s extra scope is copies out of files that the commit CREATING the
    destination did not modify. Tracing a SOURCE file backwards, the source is
    by construction modified in the commit that removed the lines, so ordinary
    `-C` already covers it. The discriminator does exist — but only FORWARDS, on
    the DESTINATION file (`-C` attributes the lifted block to the split commit,
    `-C -C` to the original), and it cannot be turned round: reverse blame
    refuses a path that does not exist at the start of the range
    (`fatal: no such path split_out.py in <C1>`, rc 128) and does no rename or
    copy detection across that boundary.

    So these are TEMPLATE assertions, which is what this repository reaches for
    when behaviour cannot tell two implementations apart — CB-41's rule verbatim
    (a Python-sampled deadline still looks fresh unless real time passes, so the
    test asserts the SQL), and the harness's guard-invocation tests are the same
    shape. **Said plainly and NOT to be read as more than it is: this pins that
    nobody removed the flag silently. It does NOT establish that `-C -C` buys
    anything under `--reverse`, and the mutation probe's `-C -C` -> `-C` mutant
    must NOT be reported as killed on the strength of it.** Whether the doubled
    flag is justified in reverse mode at all is a question for the direction
    holder against v6's own measurement record, not a decision this file makes.
    """

    def _blame_argv(self):
        src = inspect.getsource(loc._channel_a)
        return src[src.index("_git(") : src.index("budget,\n    )")]

    def test_the_doubled_copy_flag_is_present(self):
        assert self._blame_argv().count('"-C"') == 2

    def test_quote_path_is_disabled(self):
        argv = self._blame_argv()
        assert '"core.quotePath=false"' in argv

    def test_the_range_starts_at_the_anchored_commit_and_ends_at_head(self):
        assert 'f"{anchor[\'commit\']}..HEAD"' in self._blame_argv()

    def test_the_span_is_bounded_by_the_anchor(self):
        assert 'f"{line},{end}"' in self._blame_argv()
