#!/usr/bin/env python3
"""Подключает предупреждающую вставку `/simplify-traced` к проекту — тот же механизм, что в autosorter.

ЧТО ЭТО. Вставка не живёт внутри `tools/worktree-finish.sh`; она стоит СНАРУЖИ — это
`PreToolUse`-хук на инструмент Bash. Хук разбирает командную строку и опознаёт вызов
`worktree-finish`, но НЕ БЛОКИРУЕТ его: сам хук объявляет `exit: always 0 — this gate
advises, it must never block an unrelated command`, всегда возвращает 0 и отдаёт
`additionalContext` (текст-предписание), а не `permissionDecision: deny`. Хук также НЕ
ПРОВЕРЯЕТ, был ли проход `/simplify-traced` в этой сессии: он не читает стенограмму и не
хранит состояния, поэтому вставляет предписание безусловно при каждом опознанном вызове
финиша, а решение — остановиться и запустить проход или продолжить — остаётся суждением
модели.
Скрипт хука общий и живёт вне проектов: ~/.claude/hooks/simplify-traced-gate.sh

ЗАЧЕМ. Проход упрощения — суждение, а не проверка, поэтому его нельзя поставить
шагом внутрь скрипта финиша: там он стал бы источником ложных отказов на посадке.
Но и «по доброй воле» он не работает: в codebugs он тихо выпал при переносе харнеса
из autosorter (взяли скрипты и сторожа, не взяли то, что стоит снаружи) и полтора
десятка смен прошли без него, а журнал багфикс-цикла хранит два случая, где этот
проход ловил живые регрессии.

ИСПОЛЬЗОВАНИЕ.
    python3 install-simplify-gate.py                 # аудит всех проектов
    python3 install-simplify-gate.py <корень>        # сухой прогон для проекта
    python3 install-simplify-gate.py <корень> --apply

Файл настроек правится НЕРАЗРУШАЮЩЕ: существующие разделы (permissions и прочие)
не трогаются, добавляется только hooks.PreToolUse. Повторный запуск идемпотентен.
Перед записью снимается резервная копия. Вставка вступает в силу для НОВЫХ сессий —
хуки читаются при старте.
"""

import glob
import json
import os
import shutil
import sys
import time

HOOK = os.path.expanduser("~/.claude/hooks/simplify-traced-gate.sh")

ENTRY = {
    "matcher": "Bash",
    "hooks": [
        {
            "type": "command",
            "command": HOOK,
            "timeout": 5,
            "statusMessage": "Checking simplify-traced gate...",
        }
    ],
}


def _settings_paths(root):
    return [os.path.join(root, ".claude", n) for n in ("settings.json", "settings.local.json")]


def gate_state(root):
    """ЕСТЬ / нет / (нет файла настроек)."""
    seen_file = False
    for p in _settings_paths(root):
        if not os.path.isfile(p):
            continue
        seen_file = True
        try:
            if "simplify-traced-gate" in json.dumps(json.load(open(p))):
                return "ЕСТЬ"
        except Exception:
            continue
    return "нет" if seen_file else "нет настроек"


def audit():
    roots = set()
    for p in glob.glob(os.path.expanduser("~/w/*/tools/worktree-finish.sh")):
        roots.add(os.path.dirname(os.path.dirname(p)))
    print(f"{'проект':32} {'харнес':8} {'гейт':14}")
    print("-" * 56)
    missing = []
    for r in sorted(roots):
        st = gate_state(r)
        if st != "ЕСТЬ":
            missing.append(r)
        print(f"{os.path.basename(r):32} {'да':8} {st:14}")
    print()
    print(f"скрипт хука на месте: {os.path.isfile(HOOK)}")
    if missing:
        print()
        print("без гейта — подключить по одному:")
        for r in missing:
            print(f"  python3 {sys.argv[0]} {r} --apply")
    return 0


def install(root, apply):
    if not os.path.isfile(HOOK):
        print(f"ОТКАЗ: скрипта хука нет: {HOOK}")
        return 1
    path = os.path.join(root, ".claude", "settings.json")
    if not os.path.isfile(path):
        alt = os.path.join(root, ".claude", "settings.local.json")
        if os.path.isfile(alt):
            path = alt
        else:
            print(f"ОТКАЗ: нет файла настроек в {os.path.join(root, '.claude')}")
            return 1
    if gate_state(root) == "ЕСТЬ":
        print(f"{root}: гейт уже подключён — делать нечего.")
        return 0

    data = json.load(open(path))
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    pre.append(ENTRY)

    print(f"файл:        {path}")
    print(f"было ключей: {sorted(k for k in data if k != 'hooks')} (не тронуты)")
    print("добавляется: hooks.PreToolUse — перехват Bash, гейт simplify-traced")
    if not apply:
        print()
        print("СУХОЙ ПРОГОН — ничего не записано. Для записи добавьте --apply")
        return 0

    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    with open(path, "w") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print()
    print(f"ЗАПИСАНО. Резервная копия: {backup}")
    print("Гейт вступит в силу для НОВЫХ сессий (хуки читаются при старте).")
    return 0


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if not args:
        return audit()
    root = os.path.abspath(os.path.expanduser(args[0]))
    if not os.path.isdir(root):
        print(f"ОТКАЗ: нет такого каталога: {root}")
        return 1
    return install(root, "--apply" in sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
