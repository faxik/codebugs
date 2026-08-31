"""Prove every arm of the ceiling gate can actually fail.

The module is imported and its declared table overridden in memory — the test
file itself is never edited, so the check is reproducible.
"""
import importlib
import os
import sys

sys.path.insert(0, "/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer/tests")
import test_claude_md_size_ceiling as M  # noqa: E402

ROOT = "/home/faxik/w/codebugs/.worktrees/docs-t131-root-directive-layer"


def fires(label, fn, *args, expect_fail=True):
    try:
        fn(*args)
        got = "passed"
    except AssertionError:
        got = "REFUSED"
    except KeyError as e:
        got = f"KeyError{e}"
    ok = (got == "REFUSED") == expect_fail
    print(f"  [{'OK ' if ok else 'BAD'}] {label:<62} -> {got}")
    return ok


def write(rel, n, ch="x"):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(ch * n)


def rm(rel):
    p = os.path.join(ROOT, rel)
    if os.path.exists(p):
        os.remove(p)


results = []
print("M0 baseline — the real tree must be green:")
results.append(fires("root within its ceiling", M.test_file_is_within_its_ceiling, "CLAUDE.md", expect_fail=False))
results.append(fires("root ceiling not stale", M.test_declared_ceiling_is_not_stale, "CLAUDE.md", expect_fail=False))
results.append(fires("discovery not vacuous", M.test_discovery_is_not_vacuous, expect_fail=False))

print("M1 an UNDECLARED oversized nested file must be refused:")
write("src/codebugs/CLAUDE.md", 9000)
results.append(fires("undeclared 9000b vs default 8000", M.test_file_is_within_its_ceiling, "src/codebugs/CLAUDE.md"))
rm("src/codebugs/CLAUDE.md")

print("M1b the same file UNDER the default must pass:")
write("src/codebugs/CLAUDE.md", 3000)
results.append(fires("undeclared 3000b vs default 8000", M.test_file_is_within_its_ceiling, "src/codebugs/CLAUDE.md", expect_fail=False))
rm("src/codebugs/CLAUDE.md")

print("M2 a hollow (stale) ceiling must be refused:")
saved = dict(M.CEILINGS)
M.CEILINGS["CLAUDE.md"] = (500_000, "hollow")
results.append(fires("ceiling 500000 over an actual ~190441", M.test_declared_ceiling_is_not_stale, "CLAUDE.md"))
M.CEILINGS.clear(); M.CEILINGS.update(saved)

print("M3 a declared ceiling naming a missing file must be refused:")
M.CEILINGS["docs/nope/CLAUDE.md"] = (100, "bogus")
results.append(fires("declared entry with no file", M.test_declared_ceiling_names_a_file_that_exists, "docs/nope/CLAUDE.md"))
M.CEILINGS.clear(); M.CEILINGS.update(saved)

print("M4 pruning — a huge file inside .worktrees must not be discovered:")
write(".worktrees/fake/CLAUDE.md", 50000, "y")
found = M._discovered()
hit = any(r.startswith(".worktrees") for r in found)
print(f"  [{'OK ' if not hit else 'BAD'}] .worktrees pruned from discovery{'':<29} -> {'pruned' if not hit else 'LEAKED'}")
results.append(not hit)
rm(".worktrees/fake/CLAUDE.md")
os.rmdir(os.path.join(ROOT, ".worktrees/fake"))
os.rmdir(os.path.join(ROOT, ".worktrees"))

print()
print("discovered on the clean tree:", sorted(M._discovered()))
print(f"RESULT: {sum(results)}/{len(results)} arms behaved as designed")
sys.exit(0 if all(results) else 1)
