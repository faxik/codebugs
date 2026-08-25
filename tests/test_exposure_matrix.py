"""CB-155 — `.claude/plans/exposure-scripts/matrix.py` must describe its own
mode honestly in every run, not just in the default one.

WHY SUBPROCESS. `matrix.py` is a standalone script (see its own USAGE
docstring: `python3 .claude/plans/exposure-scripts/matrix.py [--ast-only]`),
never imported by this package or by anything else in `tests/`. Verifying its
printed banner therefore means running the exact command a human or another
script would run — the T-64 brief names the T-60 lesson explicitly: know
which code actually executes, and for a script that is shelled out to, that
means a real subprocess, not a call into its module from this process.

THE DEFECT (CB-155). Before this test existed, the top banner read
`HYBRID: registry for existence, AST for size` on EVERY run, including under
`--ast-only`, where the registry half is skipped entirely and both existence
and size come from the AST pass alone (CB-153's pre-fix behaviour, kept
behind the flag on purpose so its mutant probe can reproduce the old report).
A report captured under `--ast-only` and read back later — the flag's whole
reason to exist — would therefore look registry-truth when it is not, and the
16 `bench`/`sweep` capabilities CB-153 fixed would silently read as `NEITHER`
again. Same class of self-description lie CB-153 closed one level up; this
closes it at the banner.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MATRIX = _REPO_ROOT / ".claude" / "plans" / "exposure-scripts" / "matrix.py"


def _run(*args: str) -> str:
    """Run the real script as a subprocess of THIS interpreter (T-60's lesson:
    verify which code executes — for a shelled-out script that is a real
    subprocess, matching the interpreter the test itself runs under so the
    `mcp` SDK and the editable `codebugs` install are on its path exactly as
    they are for this test process)."""
    proc = subprocess.run(
        [sys.executable, str(_MATRIX), *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"matrix.py {list(args)} exited {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _banner(stdout: str) -> str:
    # The banner is the second printed line, between two `BAR` rules -- take
    # the first few lines rather than the whole (long) report.
    return "\n".join(stdout.splitlines()[:4])


class TestBannerNamesActualMode:
    """CB-155: the top-line banner must name the mode that actually ran."""

    def test_ast_only_banner_says_ast_only_not_hybrid(self):
        banner = _banner(_run("--ast-only"))
        assert "AST-ONLY" in banner, banner
        assert "HYBRID" not in banner, (
            "banner still claims HYBRID under --ast-only, where the registry "
            f"half never runs (CB-155):\n{banner}"
        )

    def test_default_run_banner_says_hybrid(self):
        banner = _banner(_run())
        assert "HYBRID" in banner, banner
        assert "AST-ONLY" not in banner, banner


class TestSelfCheckNotesItsOwnScopeUnderAstOnly:
    """CB-155's 'заодно': under --ast-only, SELF-CHECK judges AST-visible
    coverage only, not registry coverage -- the run must say so, or a
    MISMATCH there reads as the same claim it makes in the hybrid run."""

    def test_ast_only_self_check_names_its_narrower_scope(self):
        out = _run("--ast-only", "--check")
        assert "AST-visible coverage only" in out, out

    def test_default_self_check_carries_no_such_disclaimer(self):
        out = _run("--check")
        assert "AST-visible coverage only" not in out, out
