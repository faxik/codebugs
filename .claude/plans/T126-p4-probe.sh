#!/usr/bin/env bash
# P4 — build the three states in which the CLAUDE.md pins actually break.
# Runs from the worktree root. Always restores CLAUDE.md.
set -uo pipefail
WT=/home/faxik/w/codebugs/.worktrees/docs-t126-claude-md-compression
cd "$WT" || exit 1

PINS=(
  "tests/test_worktree_harness.py::TestPostMergeAlarmIsNotAGate"
  "tests/test_cli_signals.py::TestAFullDeviceReportsALostOutputAndNotBadInput::test_the_code_is_declared_in_claude_md"
)

run_pins () {
  uv run --extra dev python -m pytest "${PINS[@]}" -q 2>&1 | tail -3
}

restore () { git checkout -- CLAUDE.md; }

echo "########## BASELINE ##########"
run_pins

echo
echo "########## P4(a): drop the token 'check-then-act' from the CB-121 window, saying the same in plain words ##########"
python3 - <<'PY'
import io
p = "CLAUDE.md"
md = io.open(p, encoding="utf-8").read()
old = "in-lock re-check is a **check-then-act**."
new = "in-lock re-check looks and only then acts, with a gap between the two."
assert md.count(old) == 1, ("anchor not unique", md.count(old))
io.open(p, "w", encoding="utf-8").write(md.replace(old, new))
print("mutated: removed the literal token, kept the meaning")
PY
run_pins
restore

echo
echo "########## P4(b): a NEW line with a leading '| ' containing 'alarm', far from the table ##########"
python3 - <<'PY'
import io
p = "CLAUDE.md"
md = io.open(p, encoding="utf-8").read()
anchor = "## Milestones module"
assert md.count(anchor) == 1
# A compression could plausibly fold prose into a table anywhere in the file.
inject = anchor + "\n\n| Surface | Note |\n|---|---|\n| pull_next | fires the capacity alarm |\n"
io.open(p, "w", encoding="utf-8").write(md.replace(anchor, inject))
print("mutated: injected a table row containing 'alarm' under the LAST section")
PY
run_pins
restore

echo
echo "########## P4(c): strip the leading '| ' from an existing table row  ##########"
echo "########## EXPECTED RESULT IS THE OPPOSITE: green, row silently drops out  ##########"
python3 - <<'PY'
import io
p = "CLAUDE.md"
md = io.open(p, encoding="utf-8").read()
old = "| One integration at a time | `flock` on `.worktrees/.integrate.lock` | exit 1 |"
assert md.count(old) == 1, ("anchor not unique", md.count(old))
io.open(p, "w", encoding="utf-8").write(md.replace(old, old[2:]))
print("mutated: row 'One integration at a time' lost its leading '| '")
PY
echo "-- how many rows does the pin's own selector now see? --"
python3 -c "
import io
md=io.open('CLAUDE.md',encoding='utf-8').read()
print('rows seen by [ln for ln in md.splitlines() if ln.startswith(\"| \")]:', len([l for l in md.splitlines() if l.startswith('| ')]))
"
run_pins
restore

echo
echo "########## P4(c2): same strip, applied to the in-lock SHA re-check row the pin NAMES ##########"
python3 - <<'PY'
import io
p = "CLAUDE.md"
md = io.open(p, encoding="utf-8").read()
old = "| The tested state still matched at the moment it was re-checked | in-lock SHA re-check | exit 13 |"
assert md.count(old) == 1, ("anchor not unique", md.count(old))
io.open(p, "w", encoding="utf-8").write(md.replace(old, old[2:]))
print("mutated: the in-lock SHA re-check row lost its leading '| '")
PY
run_pins
restore

echo
echo "########## RESTORED ##########"
git status --porcelain -- CLAUDE.md; echo "(empty = clean)"
run_pins
