"""CB-326, второй круг: ДВЕ оси, которые первый круг не замерил и потому проглядел.

ЗАЧЕМ ЭТОТ ФАЙЛ. Первый круг починки проверил форму ответа замером — и всё-таки
ошибся, потому что замерил её только вдоль одной оси. Пробники первого круга
поднимают клиентский сеанс через `initialize()`, то есть согласуют СТАРУЮ
редакцию протокола, и вопрос «а как то же самое выглядит в текущей редакции»
не был задан вовсе. Этот файл существует, чтобы такой вопрос больше нельзя было
не задать: он снимает обе оси сразу и печатает их рядом.

ОСЬ ПЕРВАЯ — РЕДАКЦИЯ ПРОТОКОЛА.

  `initialize()` — старая редакция;
  `discover()`   — текущая, где у результата вызова инструмента есть
                   ОБЯЗАТЕЛЬНОЕ поле `resultType`.

Отсюда следствие, которое и сделало первую починку негодной: результат,
собранный руками из полей `content` и `isError`, в старой редакции проходит, а в
текущей клиентская библиотека отвергает его СВОИМ ЖЕ проверяльщиком, и вызов
не возвращает значение, а поднимает исключение. То есть меняется КАНАЛ, по
которому отказ доходит до вызывающего, — ровно то, что запрещено менять.

Почему замер первого круга этого не показал: словарь, который наблюдатель видит
в промежуточном слое, — это то, что чужая библиотека УЖЕ пропустила через свой
сериализатор, а не то, что безопасно вернуть ДО него. Наблюдать выход
сериализатора и делать вывод о допустимом входе — подмена вопроса, и она стоила
целой блокирующей находки.

ОСЬ ВТОРАЯ — ФОРМА ПОЛЯ АРГУМЕНТОВ.

  вызов без аргументов вовсе — клиентская библиотека НЕ ПОСЫЛАЕТ поле
                               `arguments` совсем;
  вызов с пустым отображением — поле послано и равно `{}`.

Это две разные формы на проводе, и сервер не имеет права их путать. Первый круг
проверял `isinstance(arguments, Mapping)`, отсутствующее поле давало `None`,
проверка не проходила, и запрос уходил к чужой проверке схемы — то есть самая
частая форма ровно того дефекта, ради которого заведена карта, оставалась
открытой.

КАК ЗАПУСКАТЬ (только из своего рабочего дерева, под обеими версиями):

    uv run --extra dev python tests/manual/probe_cb326_protocol_and_arguments.py
    uv run --with 'mcp==2.1.1' --extra dev python \
        tests/manual/probe_cb326_protocol_and_arguments.py
"""

from __future__ import annotations

import contextlib
import importlib.metadata as md
import pathlib
import sys
import tempfile
from typing import Any

from codebugs import db, server

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from test_cb310_refusal_text import call_over_the_wire  # noqa: E402

#: Признаки того, что текст пришёл не от этого проекта. Тот же набор, что в
#: снимке договора: одной лишь ссылки мало — библиотека может убрать ссылку, не
#: перестав говорить своим голосом.
_FOREIGN = ("errors.pydantic.dev", "validation error", "Arguments")


def _tracker(root: str):
    db.init_project(root)

    @contextlib.contextmanager
    def _conn():
        conn = db.connect(root)
        try:
            yield conn
        finally:
            conn.close()

    return _conn


def _innermost(exc: BaseException) -> BaseException:
    inner = getattr(exc, "exceptions", None)
    return _innermost(inner[0]) if inner else exc


#: Случаи. `None` в позиции аргументов означает «поле не посылать вовсе» — это и
#: есть вторая ось, и её нельзя выразить пустым словарём.
CASES: list[tuple[str, str, dict | None]] = [
    ("не подано обязательное поле", "add", {"category": "x", "file": "y", "description": "z"}),
    ("аргументов НЕТ вовсе", "add", None),
    ("аргументы — пустое отображение", "add", {}),
    (
        "пропущено И неверный тип",
        "add",
        {"category": "x", "file": "y", "severity": 5},
    ),
    (
        "только неверный тип",
        "add",
        {"severity": 5, "category": "x", "file": "y", "description": "z"},
    ),
    (
        "незаявленное имя аргумента",
        "add",
        {"severity": "low", "category": "x", "file": "y", "description": "z", "bogus": 1},
    ),
    ("доменный отказ", "update", {"finding_id": "CB-1", "status": "bogus"}),
    ("несуществующий инструмент", "no_such_tool", {"anything": 1}),
    (
        "удачный вызов",
        "add",
        {"severity": "low", "category": "x", "file": "y", "description": "z",
         "new_category": True},
    ),
]


def _one(factory: Any, tool: str, arguments: dict | None, protocol: str) -> str:
    built = server._build_server("findings", factory)
    try:
        is_error, text = call_over_the_wire(built, tool, arguments, protocol=protocol)
    except BaseException as exc:  # noqa: BLE001 — форма ответа и есть предмет замера
        inner = _innermost(exc)
        return f"ПОДНЯЛ {type(inner).__name__}: {str(inner)[:110]}"
    marks = [m for m in _FOREIGN if m in text] or ["-"]
    return f"вернул is_error={is_error} чужое={','.join(marks)} текст={text[:88]!r}"


def main() -> None:
    print(f"# mcp={md.version('mcp')} pydantic={md.version('pydantic')}")
    print("#")
    for protocol in ("initialize", "discover"):
        print("=" * 78)
        print(f"РЕДАКЦИЯ ПРОТОКОЛА: {protocol}()")
        print("=" * 78)
        for label, tool, arguments in CASES:
            with tempfile.TemporaryDirectory() as root:
                factory = _tracker(root)
                print(f"--- {label} ({tool}) ---")
                print(f"  {_one(factory, tool, arguments, protocol)}")
        print()


if __name__ == "__main__":
    main()
