#!/usr/bin/env bash
# Т-126, предпосылка П4 (§4 брифа): построить состояния, в которых пины,
# читающие `CLAUDE.md`, ломаются по-настоящему — и одно, в котором защита
# исчезает БЕЗЗВУЧНО.
#
# Скрипт мутирует `CLAUDE.md`, гоняет пины и восстанавливает файл. Три вещи
# сделаны намеренно и стоят объяснения, потому что каждая закрывает способ,
# которым такая проба вредит:
#
#   1. ПРЕДПОЛЁТНАЯ ПРОВЕРКА ЧИСТОТЫ. Восстановление здесь —
#      `git checkout -- CLAUDE.md`, то есть оно СТИРАЕТ незакоммиченные
#      правки. Репозиторий уже платил за эту форму (CB-173: чужая
#      несохранённая работа уничтожена пять раз), и ответом был
#      `tests/manual/mutation_guard.py::require_clean_tree`. Он и вызывается
#      ниже, вместо того чтобы писать шестую копию той же проверки. Страж
#      закрыт наглухо: нечитаемый git считается ГРЯЗНЫМ, а не чистым.
#   2. `trap restore EXIT`. Без него сохранность файла держалась на том, что
#      в теле нет ни `set -e`, ни `exit`, — то есть на случайности, которую
#      снимает первая же будущая правка вида `run_pins || exit 1`. Ctrl-C
#      посреди прогона пинов оставлял бы мутированный `CLAUDE.md` на диске.
#   3. ОДНА функция мутации вместо трёх копий heredoc'а. Три почти одинаковых
#      блока — это три места, куда придётся вносить одну и ту же поправку.
set -uo pipefail

WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WT" || exit 1

PINS=(
  "tests/test_worktree_harness.py::TestPostMergeAlarmIsNotAGate"
  "tests/test_cli_signals.py::TestAFullDeviceReportsALostOutputAndNotBadInput::test_the_code_is_declared_in_claude_md"
)

run_pins () { uv run --extra dev python -m pytest "${PINS[@]}" -q 2>&1 | tail -3; }
restore ()  { git checkout -- CLAUDE.md; }

# --- предполётный страж: см. пункт 1 в шапке -------------------------------
python3 - "$WT" <<'PY' || exit 1
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "tests" / "manual"))
from mutation_guard import require_clean_tree
require_clean_tree(["CLAUDE.md"], cwd=sys.argv[1])
print("страж: CLAUDE.md чист, мутировать безопасно")
PY

trap restore EXIT

# replace_once <старое> <новое> <пояснение>
# Требует, чтобы якорь встречался РОВНО один раз: проба, попавшая не туда,
# куда думала, доказывает не то, что о ней напишут.
replace_once () {
  python3 - "$1" "$2" "$3" <<'PY'
import io, sys
old, new, note = sys.argv[1], sys.argv[2], sys.argv[3]
md = io.open("CLAUDE.md", encoding="utf-8").read()
assert md.count(old) == 1, f"якорь не уникален: {md.count(old)} вхождений"
io.open("CLAUDE.md", "w", encoding="utf-8").write(md.replace(old, new))
print(f"мутация: {note}")
PY
}

rows_seen () {
  python3 -c "
import io
md = io.open('CLAUDE.md', encoding='utf-8').read()
print('строк, которые видит выборка пина:',
      len([l for l in md.splitlines() if l.startswith('| ')]))
"
}

echo "########## БАЗА ##########"
run_pins

echo
echo "########## П4(а): из окна вокруг CB-121 убран токен check-then-act, смысл сказан по-человечески ##########"
echo "########## ожидается КРАСНОЕ ##########"
replace_once \
  'in-lock re-check is a **check-then-act**.' \
  'in-lock re-check looks and only then acts, with a gap between the two.' \
  'снят литерал, смысл сохранён'
run_pins
restore

echo
echo "########## П4(б): новая строка с ведущим '| ', содержащая alarm, за 200 КБ от таблицы ##########"
echo "########## ожидается КРАСНОЕ ##########"
replace_once \
  '## Milestones module' \
  '## Milestones module

| Surface | Note |
|---|---|
| pull_next | fires the capacity alarm |' \
  'вставлена строка таблицы со словом alarm под последним разделом'
run_pins
restore

echo
echo "########## П4(в): у обычной строки таблицы снят ведущий '| ' ##########"
echo "########## ожидается ЗЕЛЁНОЕ — беззвучное выпадение ##########"
replace_once \
  '| One integration at a time | `flock` on `.worktrees/.integrate.lock` | exit 1 |' \
  'One integration at a time | `flock` on `.worktrees/.integrate.lock` | exit 1 |' \
  'строка "One integration at a time" потеряла ведущий "| "'
rows_seen
run_pins
restore

echo
echo "########## П4(в2): тот же приём к строке, которую пин ищет ПО ТЕКСТУ ##########"
echo "########## ожидается КРАСНОЕ — и это показывает, что защищена ровно одна строка из пятнадцати ##########"
replace_once \
  '| The tested state still matched at the moment it was re-checked | in-lock SHA re-check | exit 13 |' \
  'The tested state still matched at the moment it was re-checked | in-lock SHA re-check | exit 13 |' \
  'строка in-lock SHA re-check потеряла ведущий "| "'
run_pins
restore

echo
echo "########## ВОССТАНОВЛЕНО ##########"
git status --porcelain -- CLAUDE.md
echo "(пусто = чисто; сам факт зелени пинов на восстановленном файле уже показан блоком БАЗА)"
