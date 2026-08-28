"""CB-244 — `tools/install-simplify-gate.py` must not call a project connected on a substring.

WHAT THE DEFECT WAS. `gate_state` serialised the whole settings file with
`json.dumps` and asked whether the hook's NAME occurred anywhere in the result.
So a settings file mentioning `simplify-traced-gate` in `permissions`, in a
comment string, or in a `PreToolUse` entry pointing at a DIFFERENT script all
answered `ЕСТЬ`. The consequence is quiet and in the worse direction: a project
with no insert vanishes from the audit's "connect this one" list, so it stops
being visible as needing work. Measured before the fix: a settings file carrying
the name only under `permissions`, with no `hooks` key at all, returned `ЕСТЬ`.

WHAT THIS FILE HOLDS. Two properties, and they are different in kind.

  * The check is STRUCTURAL — it asks for the shape `install()` writes — so it
    has to distinguish an entry that connects the gate from one that merely
    mentions it. The table below is the population of ways this can go wrong,
    each row saying which answer is correct and why.
  * The check is THREE-VALUED plus one. `os.path.isfile` and a bare
    `except Exception: continue` together turned *could not look* into *not
    connected*, in the one function whose entire subject is whether we looked
    correctly. That is CB-203/CB-218/CB-224's ratified answer applied here: an
    undetermined read returns a fourth value carrying a human reason.

THE MODULE IS LOADED BY PATH. `tools/install-simplify-gate.py` carries hyphens,
so it is not importable by name; `importlib` off its real path is the only way,
and it is also the honest one — the test then judges the file the operator runs.

NOTHING HERE TOUCHES THE OWNER'S `.claude/settings.json`. Every case builds its
own settings file under `tmp_path`. The hook's own path is read from the module
(`HOOK`) rather than spelled again, so the tests cannot drift from the value
`install()` actually writes and do not depend on the hook script existing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat
import sys
import types

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
INSTALLER = TOOLS / "install-simplify-gate.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("install_simplify_gate", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def isg() -> types.ModuleType:
    return _load()


def write_settings(project: pathlib.Path, name: str, data) -> pathlib.Path:
    """Put one settings file into a project root; `data` is JSON, or raw text verbatim.

    A free function rather than a method on the fixture: the fixture then hands
    back a plain `pathlib.Path`, so `str(root)` and `root / "tools"` are the
    real thing instead of two delegating wrappers a reader has to check.
    """
    target = project / ".claude" / name
    target.write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


@pytest.fixture
def root(tmp_path: pathlib.Path):
    """A project root with an empty `.claude/`.

    Cleanup restores permissions on the way out: three cases below remove them
    deliberately, and pytest's own tmp_path reaper would otherwise fail on the
    directory rather than on the test.
    """
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    yield project
    for p in sorted(project.rglob("*"), reverse=True):
        try:
            os.chmod(p, stat.S_IRWXU)
        except OSError:
            pass
    try:
        os.chmod(project / ".claude", stat.S_IRWXU)
    except OSError:
        pass


def _entry(isg: types.ModuleType, command: str | None = None, matcher: str = "Bash") -> dict:
    """The shape `install()` writes, with one field swappable per case."""
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": isg.HOOK if command is None else command,
                "timeout": 5,
                "statusMessage": "Checking simplify-traced gate...",
            }
        ],
    }


class TestGateStateRecognisesTheStructureInstallWrites:
    """The rows that must answer `ЕСТЬ`, and the rows that must not.

    The two halves are one class on purpose: the value of a structural check is
    exactly that it separates them, and a file holding only the refusals could
    be satisfied by a function that always says `нет`.
    """

    def test_the_entry_install_writes_is_recognised(self, isg, root) -> None:
        write_settings(root, "settings.json", {"hooks": {"PreToolUse": [_entry(isg)]}})
        assert isg.gate_state(str(root)) == isg.STATE_PRESENT

    def test_the_entry_is_recognised_beside_other_pretooluse_entries(self, isg, root) -> None:
        """A real settings file has other hooks; the search is a scan, not a peek at [0]."""
        write_settings(
            root,
            "settings.json",
            {"hooks": {"PreToolUse": [_entry(isg, matcher="Write"), _entry(isg)]}},
        )
        assert isg.gate_state(str(root)) == isg.STATE_PRESENT

    def test_the_entry_is_recognised_in_settings_local(self, isg, root) -> None:
        """`install()` falls back to `settings.local.json`, so a gate can legitimately live there.

        Named in the brief beside the four false-positive rows precisely
        because it is the one that must keep answering `ЕСТЬ`: a structural
        check that refused it would have traded one wrong answer for another.
        """
        write_settings(root, "settings.local.json", {"hooks": {"PreToolUse": [_entry(isg)]}})
        assert isg.gate_state(str(root)) == isg.STATE_PRESENT

    # --- the ways a file can MENTION the hook without connecting it -------

    def test_a_mention_only_in_permissions_is_not_a_connection(self, isg, root) -> None:
        """THE MEASURED DEFECT. This exact file returned `ЕСТЬ` before the fix.

        A `permissions` entry allowing the hook script to be RUN is an ordinary
        thing for a settings file to carry, and it says nothing about whether
        the PreToolUse insert is installed.
        """
        write_settings(
            root,
            "settings.json",
            {"permissions": {"allow": [f"Bash({isg.HOOK}:*)"]}},
        )
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT

    def test_a_mention_in_a_comment_string_is_not_a_connection(self, isg, root) -> None:
        write_settings(
            root,
            "settings.json",
            {"_comment": "simplify-traced-gate — подключить, когда дойдут руки"},
        )
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT

    def test_a_pretooluse_entry_with_another_matcher_is_not_a_connection(self, isg, root) -> None:
        """The gate exists to intercept Bash; on `Write` it can never see a finish."""
        write_settings(
            root, "settings.json", {"hooks": {"PreToolUse": [_entry(isg, matcher="Write")]}}
        )
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT

    def test_the_right_matcher_pointing_at_another_script_is_not_a_connection(
        self, isg, root
    ) -> None:
        """The row that separates a structural check from a slightly better substring one.

        Everything here is correct except the one field that decides: the
        matcher is `Bash`, the entry is well formed, and the hook's name even
        appears in `statusMessage`. What runs is a different script.
        """
        write_settings(
            root,
            "settings.json",
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": os.path.expanduser("~/.claude/hooks/other.sh"),
                                    "statusMessage": "simplify-traced-gate",
                                }
                            ],
                        }
                    ]
                }
            },
        )
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT

    def test_the_entry_under_posttooluse_is_not_a_connection(self, isg, root) -> None:
        """A hook that runs AFTER the command cannot advise stopping before it."""
        write_settings(root, "settings.json", {"hooks": {"PostToolUse": [_entry(isg)]}})
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT

    @pytest.mark.parametrize(
        "malformed",
        [
            pytest.param({"hooks": {"PreToolUse": {"matcher": "Bash"}}}, id="pretooluse-not-list"),
            pytest.param(
                {"hooks": {"PreToolUse": [{"matcher": "Bash", "command": "…"}]}},
                id="entry-without-inner-hooks",
            ),
            pytest.param({"hooks": []}, id="hooks-not-mapping"),
            pytest.param([], id="settings-not-mapping"),
            pytest.param({}, id="empty-settings"),
        ],
    )
    def test_a_shape_that_is_not_the_entry_answers_no_rather_than_raising(
        self, isg, root, malformed
    ) -> None:
        """A settings file is a foreign artefact; a wrong shape is a `нет`, never a traceback.

        `нет` and not the undetermined value: the file was read and parsed, so
        the question WAS answered — what is there simply is not the insert.
        """
        write_settings(root, "settings.json", malformed)
        assert isg.gate_state(str(root)) == isg.STATE_ABSENT


class TestGateStateSeparatesCouldNotLookFromNotConnected:
    """The third value, and the reason it defaults rather than enumerates.

    `os.path.isfile` swallows every `OSError` beneath it and `except Exception:
    continue` swallowed the rest, so an unreadable file, an unreadable
    `.claude`, a directory sitting where the settings file should be and a file
    that is not JSON all came back as `нет` — *could not look*, written as *not
    connected*, in a function whose whole subject is whether we looked
    correctly. Absence is now exactly one condition (`FileNotFoundError`), so a
    mechanism nobody enumerated lands in the undetermined value by construction.
    """

    def test_no_settings_files_at_all_is_its_own_answer(self, isg, root) -> None:
        assert isg.gate_state(str(root)) == isg.STATE_NO_SETTINGS

    def test_a_file_that_is_not_json_is_undetermined(self, isg, root) -> None:
        write_settings(root, "settings.json", "{ это не JSON")
        state = isg.gate_state(str(root))
        assert state.startswith(isg.STATE_UNREADABLE_PREFIX), state
        assert "settings.json" in state, "the fourth value must name what it could not read"

    def test_an_unreadable_settings_file_is_undetermined(self, isg, root) -> None:
        path = write_settings(root, "settings.json", {"hooks": {"PreToolUse": [_entry(isg)]}})
        os.chmod(path, 0o000)
        if os.access(path, os.R_OK):
            pytest.skip("running as a user that ignores the read bit")
        assert isg.gate_state(str(root)).startswith(isg.STATE_UNREADABLE_PREFIX)

    def test_an_unreadable_claude_directory_is_undetermined_not_absent(self, isg, root) -> None:
        """The half `os.path.isfile` hid, and the worse of the two.

        With no execute bit on `.claude`, the old code's stat failed, the file
        read as absent, and the project was reported as having no settings at
        all — a confident statement about a directory nobody could enter.
        """
        write_settings(root, "settings.json", {"hooks": {"PreToolUse": [_entry(isg)]}})
        os.chmod(root / ".claude", 0o000)
        if os.access(root / ".claude", os.X_OK):
            pytest.skip("running as a user that ignores the execute bit")
        assert isg.gate_state(str(root)).startswith(isg.STATE_UNREADABLE_PREFIX)

    def test_a_directory_where_the_settings_file_belongs_is_undetermined(self, isg, root) -> None:
        """Not `FileNotFoundError`, so not absence — something IS at that name."""
        (root / ".claude" / "settings.json").mkdir()
        assert isg.gate_state(str(root)).startswith(isg.STATE_UNREADABLE_PREFIX)

    def test_a_real_entry_in_the_other_file_outranks_an_unreadable_one(self, isg, root) -> None:
        """`ЕСТЬ` is affirmative proof and beats *could not look* — the ordering is a decision.

        Claude Code reads both files, so an insert in either one really is
        connected. The undetermined value only outranks the two NEGATIVE
        answers, which is the fail-closed half: an absence nobody could
        establish must not be reported as established.
        """
        write_settings(root, "settings.json", "{ сломано")
        write_settings(root, "settings.local.json", {"hooks": {"PreToolUse": [_entry(isg)]}})
        assert isg.gate_state(str(root)) == isg.STATE_PRESENT


class TestAuditPromisesOnlyWhatItDoes:
    """The text half of CB-244, and the audit's own handling of the fourth value."""

    def test_the_usage_line_no_longer_promises_all_projects(self, isg) -> None:
        """`audit()` globs `~/w/*/tools/worktree-finish.sh`; the docstring said "all projects".

        The card's verdict was to fix the PROMISE rather than widen the sweep —
        the reach is sufficient for the question being asked — so this asserts
        that the two agree, by reading the glob out of the source rather than
        restating it.
        """
        source = INSTALLER.read_text(encoding="utf-8")
        usage = next(ln for ln in isg.__doc__.splitlines() if "install-simplify-gate.py  " in ln)
        assert "всех проектов" not in usage, usage
        assert "харнес" in usage, usage
        assert "~/w/*/tools/worktree-finish.sh" in source, (
            "the promise was narrowed to a sweep this file no longer performs"
        )

    def test_an_unreadable_root_is_not_offered_an_apply_command(
        self, isg, root, capsys, monkeypatch
    ) -> None:
        """A repair command printed for a file we could not read is a confident wrong answer.

        `install()` would walk into the same failure, so the audit separates
        the two lists: roots that are genuinely unconnected get the `--apply`
        line, roots nobody could look at get named and nothing else.
        """
        write_settings(root, "settings.json", "{ сломано")
        monkeypatch.setattr(isg.glob, "glob", lambda _pattern: [str(root / "tools" / "x.sh")])
        isg.audit()
        out = capsys.readouterr().out
        assert isg.STATE_UNREADABLE_PREFIX in out
        assert "--apply" not in out, out
        assert "не смог посмотреть" in out, out


class TestTheInstallerIsTheSourceOfItsOwnPredicate:
    """The structural check must be derived from `install()`, not from a copy of its shape.

    Two copies of one rule are one edit apart from disagreeing, and here the
    disagreement would be invisible: the audit would report a state the
    installer does not produce. So the predicate reads `ENTRY` and `HOOK`, and
    this test fails if it stops doing so.
    """

    def test_the_predicate_reads_the_entry_the_installer_writes(self, isg) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        body = source.split("def _entry_connects_the_gate", 1)[1].split("\ndef ", 1)[0]
        assert 'ENTRY["matcher"]' in body, (
            "the matcher is spelled again instead of being read from ENTRY"
        )
        assert "HOOK" in body, "the command is spelled again instead of being read from HOOK"

    def test_the_serialise_the_whole_file_form_is_gone(self, isg) -> None:
        """The exact spelling of the defect, refused by name.

        A substring test over `json.dumps(json.load(...))` is the shape CB-244
        is about; if it ever returns, every case in this file that asserts
        `нет` would go red too — this is the cheap, legible one.
        """
        source = INSTALLER.read_text(encoding="utf-8")
        code = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
        assert "json.dumps(json.load(" not in code, (
            "gate_state is serialising the whole settings file again"
        )


def test_the_module_under_test_is_the_file_operators_run() -> None:
    """A fixture that does not assert its own setup is how this repo shipped a vacuous test.

    `importlib` off a path fails silently in exactly one interesting way — a
    typo'd path raises here rather than three classes later — so the existence
    of the file is asserted where a reader can see it.
    """
    assert INSTALLER.is_file(), INSTALLER
    assert sys.version_info >= (3, 11)
