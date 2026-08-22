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

    def test_it_adds_no_mcp_tool_so_the_wire_golden_cannot_move(self):
        """This unit registers no tools, and that is a scope statement.

        `SERVER_NAMES` gaining an entry does not create a tool — the mode filter
        simply returns an empty provider list — so the golden must be untouched.
        Asserted here as well as by the golden test, because "the golden did not
        move" is only reassuring if something says why it should not have.
        """
        names = [p.name for p in db.get_tool_providers()]
        assert "loc" not in names
        assert db.get_tool_providers(mode="loc") == []
        assert db.get_cli_providers(mode="loc") == []


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
