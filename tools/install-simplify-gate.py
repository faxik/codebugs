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
    python3 install-simplify-gate.py                 # аудит проектов с харнесом рабочих деревьев под ~/w
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


# Четыре ответа `gate_state`, вынесенные в константы, потому что их читает не
# только человек: `audit()` решает по ним, в какой список попадает проект, а
# тесты сравниваются с ними буквально. Два экземпляра одной строковой литеры —
# это одна правка до расхождения.
STATE_PRESENT = "ЕСТЬ"
STATE_ABSENT = "нет"
STATE_NO_SETTINGS = "нет настроек"
# Четвёртый ответ несёт причину, поэтому он не литерал, а ПРЕФИКС: целиком его
# строит `_unreadable`, а узнаёт по началу `audit()`.
STATE_UNREADABLE_PREFIX = "не смог прочитать"


def _unreadable(reason):
    return f"{STATE_UNREADABLE_PREFIX} ({reason})"


def _entry_connects_the_gate(entry):
    """Несёт ли одна запись `hooks.PreToolUse` ровно ту вставку, что пишет `install()`.

    Проверка выведена из `ENTRY` и из того, как `install()` его кладёт, а не из
    образца в чьём-либо тексте: `install()` добавляет в список `PreToolUse`
    запись с `matcher` = `ENTRY["matcher"]`, у которой во вложенном списке
    `hooks` лежит словарь с `command` = `HOOK`. Это и спрашивается.

    ПОЧЕМУ НЕ ПОДСТРОКА ПО ВСЕМУ ФАЙЛУ (CB-244). Прежняя проверка сериализовала
    файл настроек целиком и искала в нём имя хука, поэтому ЛЮБОЕ упоминание
    имени — в разделе `permissions`, в комментарии-строке, в записи `PreToolUse`
    с другим `matcher` или с `command`, указывающим на другой скрипт, — читалось
    как «подключено». Замерено: файл настроек, несущий имя хука только в
    `permissions` и НЕ несущий `hooks.PreToolUse`, возвращал `ЕСТЬ`. Ошибка
    молчаливая и в худшую сторону: проект без вставки исчезал из списка тех,
    где её надо подключить.

    ГРАНИЦА, НАЗВАННАЯ ВСЛУХ. `command` сравнивается с `HOOK` ТОЧНО, а `HOOK`
    уже развёрнут `expanduser`. Значит запись, написанная руками как
    `~/.claude/hooks/simplify-traced-gate.sh`, прочитается как «нет». Это
    сознательный отказ в безопасную сторону: признать её значило бы утверждать,
    что такое написание работает, а этого мы не мерили. Цена — если после
    такого ответа запустить `--apply`, в файле окажется вторая запись; вставка
    предупреждает и не блокирует, поэтому сработает дважды и ничего не сломает.
    """
    if not isinstance(entry, dict) or entry.get("matcher") != ENTRY["matcher"]:
        return False
    inner = entry.get("hooks")
    if not isinstance(inner, list):
        return False
    return any(isinstance(h, dict) and h.get("command") == HOOK for h in inner)


def _file_state(path):
    """Один файл настроек: `ЕСТЬ` / `нет` / `нет настроек` / «не смог прочитать».

    ЧИТАЕМ, А НЕ СПРАШИВАЕМ «ЕСТЬ ЛИ ФАЙЛ». Прежний код делал `os.path.isfile`
    и лишь потом открывал. `os.path.isfile` глотает любой `OSError`, поэтому
    «каталог `.claude` без права входа» приходил написанным как «файла нет» —
    тот же дефект, что и подстрочная проверка, только с другим знаком, в двух
    строках выше неё. Открываем сразу и разбираем НЕУДАЧУ; заодно исчезает
    промежуток между проверкой и открытием.

    ОТСУТСТВИЕМ считается РОВНО одно условие — `FileNotFoundError`, то есть
    ENOENT на самом имени. Всё прочее (нет прав, на этом имени каталог,
    петля симлинков, слишком длинное имя, файл не разбирается как JSON) —
    четвёртое значение с человеческой причиной. Так механизм, которого никто не
    перечислял, попадает в «не смог посмотреть» ПО ПОСТРОЕНИЮ, а не по тому,
    угадал ли автор список ошибок. Эта форма ответа ратифицирована в этом
    репозитории трижды — CB-203, CB-218, CB-224.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return STATE_NO_SETTINGS
    except OSError as exc:
        return _unreadable(f"{path}: {exc.strerror or exc}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _unreadable(f"{path}: не разбирается как JSON: {exc}")
    pre = (data.get("hooks") or {}).get("PreToolUse") if isinstance(data, dict) else None
    if isinstance(pre, list) and any(_entry_connects_the_gate(e) for e in pre):
        return STATE_PRESENT
    return STATE_ABSENT


def _state_priority(state):
    """Порядок, в котором ответ по одному файлу побеждает ответ по другому.

    Он ЕСТЬ КОД, а не проза над функцией, и это сознательно: перечень
    приоритетов, записанный словами и продублированный ручной бухгалтерией из
    двух флагов, — ровно тот «второй самодельный порядок», который в этом
    репозитории уже расходился с первым (`rank_case_sql` / `severity_rank`).
    Пятое состояние, если оно когда-нибудь появится, добавляется здесь одной
    строкой и нигде больше.
    """
    if state == STATE_PRESENT:
        return 0
    if state.startswith(STATE_UNREADABLE_PREFIX):
        return 1
    if state == STATE_ABSENT:
        return 2
    return 3  # STATE_NO_SETTINGS


def gate_state(root):
    """ЕСТЬ / нет / нет настроек / не смог прочитать (причина).

    Складывает ответы по двум файлам (`settings.json` и `settings.local.json`),
    беря наивысший по `_state_priority`, и этот порядок — решение, а не
    удобство:

    * `ЕСТЬ` побеждает всё: вставка, лежащая в ЛЮБОМ из двух файлов, — это
      подключённая вставка, потому что оба читает сам Claude Code. Это
      утвердительное доказательство, и оно сильнее любого «не смог».
    * «не смог прочитать» побеждает «нет» и «нет настроек»: отсутствие, которое
      не удалось установить, не имеет права выглядеть установленным.
    * «нет настроек» — только когда ОБА файла отсутствуют по ENOENT.

    `min` возвращает ПЕРВЫЙ из равных, поэтому при двух нечитаемых файлах
    причина называется по `settings.json` — тот же выбор, что делала прежняя
    форма, и он важен только тем, что определён.
    """
    return min((_file_state(p) for p in _settings_paths(root)), key=_state_priority)


def audit():
    """Обход ПРОЕКТОВ С ХАРНЕСОМ РАБОЧИХ ДЕРЕВЬЕВ ПОД `~/w` — не всех проектов.

    Обещание сужено до правды (CB-244). Область обзора менять не надо: для
    нынешней задачи — «у кого из проектов, живущих по этому харнесу, вставка не
    подключена» — её достаточно, а ложным было ОБЕЩАНИЕ. Замер держателя смены
    17: под `~/w` тринадцать каталогов несут трекер `.codebugs` и лишь восемь —
    харнес, то есть пять проектов с трекером сюда не попадают ПО ПОСТРОЕНИЮ.
    """
    roots = set()
    for p in glob.glob(os.path.expanduser("~/w/*/tools/worktree-finish.sh")):
        roots.add(os.path.dirname(os.path.dirname(p)))
    print(f"{'проект':32} {'харнес':8} {'гейт':14}")
    print("-" * 56)
    missing = []
    unreadable = []
    for r in sorted(roots):
        st = gate_state(r)
        if st.startswith(STATE_UNREADABLE_PREFIX):
            # Разведено с `missing` НАМЕРЕННО. Такой корень тоже требует работы,
            # но напечатать рядом с ним `--apply` значило бы выдать уверенное
            # указание про файл, который мы не смогли прочитать: `install()`
            # упрётся в то же самое место. Названная неизвестность дешевле
            # неверного совета.
            unreadable.append(r)
        elif st != STATE_PRESENT:
            missing.append(r)
        print(f"{os.path.basename(r):32} {'да':8} {st:14}")
    print()
    print(f"скрипт хука на месте: {os.path.isfile(HOOK)}")
    if missing:
        print()
        print("без гейта — подключить по одному:")
        for r in missing:
            print(f"  python3 {sys.argv[0]} {r} --apply")
    if unreadable:
        print()
        print("не смог посмотреть — разберитесь руками, состояние неизвестно:")
        for r in unreadable:
            print(f"  {r}")
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
    if gate_state(root) == STATE_PRESENT:
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
