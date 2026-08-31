"""Prove every arm of the ceiling gate can actually fail.

The module is imported and its declared table overridden in memory — the test
file itself is never edited, so the check is reproducible.
"""
import os
import subprocess
import sys

ROOT = "/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer"
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_claude_md_size_ceiling as M  # noqa: E402


def fires(label, fn, *args, expect_fail=True):
    try:
        fn(*args)
        got = "passed"
    except AssertionError:
        got = "REFUSED"
    ok = (got == "REFUSED") == expect_fail
    print(f"  [{'OK ' if ok else 'BAD'}] {label:<58} -> {got}")
    return ok


def git(*a):
    subprocess.run(["git", "-C", ROOT, *a], check=True, capture_output=True)


def write(rel, n, ch="x"):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(ch * n)


results = []
saved = dict(M.CEILINGS)

print("M0 baseline — the real tree must be green:")
results.append(fires("discovery not vacuous", M.test_discovery_is_not_vacuous, expect_fail=False))
results.append(fires("root within ceiling", M.test_file_is_within_its_ceiling, "CLAUDE.md", expect_fail=False))
results.append(fires("no stale ceiling", M.test_no_declared_ceiling_is_stale, expect_fail=False))
results.append(fires("no missing file", M.test_no_declared_ceiling_names_a_missing_file, expect_fail=False))
results.append(fires("every reason present", M.test_every_declared_ceiling_carries_a_reason, expect_fail=False))

print("M1 a TRACKED undeclared oversized nested file must be refused:")
write("src/codebugs/CLAUDE.md", 9000)
git("add", "--", "src/codebugs/CLAUDE.md")
results.append(fires("tracked undeclared 9000b vs default 8000",
                     M.test_file_is_within_its_ceiling, "src/codebugs/CLAUDE.md"))

print("M1b the same file at 3000b must pass:")
write("src/codebugs/CLAUDE.md", 3000)
results.append(fires("tracked undeclared 3000b vs default 8000",
                     M.test_file_is_within_its_ceiling, "src/codebugs/CLAUDE.md", expect_fail=False))
git("reset", "-q", "--", "src/codebugs/CLAUDE.md")

print("M2 an UNTRACKED file is invisible (the stated cost of asking git):")
seen = "src/codebugs/CLAUDE.md" in M._discovered()
print(f"  [{'OK ' if not seen else 'BAD'}] untracked 3000b file not discovered{'':<21} -> "
      f"{'invisible' if not seen else 'DISCOVERED'}")
results.append(not seen)
os.remove(os.path.join(ROOT, "src/codebugs/CLAUDE.md"))

print("M3 a hollow (stale) ceiling must be refused:")
M.CEILINGS["CLAUDE.md"] = (500_000, "hollow")
results.append(fires("ceiling 500000 over an actual ~190441", M.test_no_declared_ceiling_is_stale))
M.CEILINGS.clear(); M.CEILINGS.update(saved)

print("M4 a declared ceiling naming a missing file must be refused:")
M.CEILINGS["docs/nope/CLAUDE.md"] = (100, "bogus")
results.append(fires("declared entry with no tracked file",
                     M.test_no_declared_ceiling_names_a_missing_file))
M.CEILINGS.clear(); M.CEILINGS.update(saved)

print("M5 a blank reason must be refused:")
M.CEILINGS["CLAUDE.md"] = (194_000, "   ")
results.append(fires("reason is whitespace", M.test_every_declared_ceiling_carries_a_reason))
M.CEILINGS.clear(); M.CEILINGS.update(saved)

print("M6 THE POST-SPLIT WORLD: a pre-split ceiling over a split file must be refused.")
print("   (this is the naказ made executable — the comment in the test is not the mechanism)")
write("docs/postsplit/CLAUDE.md", 36_149)
git("add", "--", "docs/postsplit/CLAUDE.md")
M.CEILINGS["docs/postsplit/CLAUDE.md"] = (199_123, "pre-split number left behind")
results.append(fires("root at 36149 under a stale ceiling of 199123",
                     M.test_no_declared_ceiling_is_stale))
M.CEILINGS.clear(); M.CEILINGS.update(saved)
git("reset", "-q", "--", "docs/postsplit/CLAUDE.md")
os.remove(os.path.join(ROOT, "docs/postsplit/CLAUDE.md"))
os.rmdir(os.path.join(ROOT, "docs/postsplit"))

print()
print("discovered on the clean tree:", sorted(M._discovered()))
print(f"RESULT: {sum(results)}/{len(results)} arms behaved as designed")
sys.exit(0 if all(results) else 1)
