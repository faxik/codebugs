"""CB-316: снимок наблюдаемого КЛИЕНТОМ договора об ошибках у поверхности создания требования.

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Пункт 5 критерия приёмки пакета П-1 требует
зафиксировать договор ДО правки и сверить ПОСЛЕ, и требует этого не из
бюрократии: `src/codebugs/CLAUDE.md` сам признаёт, что эталонный снимок схемы
протокола (`tests/golden/mcp_schema.json`) не является гейтом на ФОРМУ ОТВЕТА —
никакой `outputSchema` не снимается, а живая схема несёт
`additionalProperties: True`. Значит внутренний набор тестов может остаться
зелёным при изменении того, что видит клиент, и единственный способ это
заметить — снять два снимка и сравнить их построчно.

ЧТО ИМЕННО СНИМАЕТСЯ. Шесть сценариев на двух поверхностях:

- удачное создание;
- повторный идентификатор (половина (а) карты CB-316);
- недопустимый приоритет (половина (б));
- недопустимый статус (та же половина (б), вторая закрытая словарная колонка);
- отсутствующий трекер (единственный сценарий, где отказ поднимается ВНЕ
  области действия помощника `cli.domain_errors` — он приходит из открытия
  трекера, и его ловит внешняя арка `cli.main`);
- незаявленное имя аргумента — только протокол, потому что у командной строки
  за это отвечает `argparse`, а не пакет.

Для командной строки записывается код возврата процесса и ФОРМА того, что
ушло в поток ошибок: `traceback` (аварийная распечатка), `one-line` (ровно одна
непустая строка), `multi-line`, `empty`. Для протокола записывается флаг ошибки
в ответе, форма текста и сам текст. Плюс — отдельной колонкой — КЛАСС
исключения, выходящего из тела инструмента через адаптер
`server._refusal_reaches_the_client`. Эта колонка добавлена намеренно: под
библиотекой `mcp` версии 2.0.0 SDK приписывает текст ЛЮБОГО исключения к
своему сообщению, поэтому по одному лишь тексту на этой версии починку не
отличить от совпадения, а класс — решение самого пакета и от версии не зависит.

КАК ЗАПУСКАТЬ (только из своего рабочего дерева):

    uv run --extra dev python tests/manual/snapshot_cb316_error_contract.py > <файл>

Пути к временным каталогам нормализуются в `<TMP>`, чтобы два прогона можно
было сравнить обычным `diff`.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from mcp.server.mcpserver import MCPServer

from codebugs import db, server

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from test_cb310_refusal_text import call_over_the_wire  # noqa: E402


# --- общее ---------------------------------------------------------------


def _shape_of_stream(text: str) -> str:
    """Форма того, что команда написала в поток ошибок."""
    if text.startswith("Traceback (most recent call last):"):
        return "traceback"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "empty"
    if len(lines) == 1:
        return "one-line"
    return "multi-line"


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _normalize(text: str, root: str) -> str:
    """Убрать из текста координаты этого прогона, иначе снимки несравнимы.

    Нормализуются две вещи: временный каталог (у каждого прогона свой) и метки
    времени в удачном ответе (они меняются каждую секунду). Без второго `diff`
    показывал бы расхождение на успешном сценарии всегда — и настоящее
    расхождение утонуло бы в шуме.
    """
    out = text.replace(os.path.realpath(root), "<TMP>").replace(root, "<TMP>")
    return _TIMESTAMP.sub("<TS>", out)


# --- поверхность командной строки ---------------------------------------


def _cli(root: str, *argv: str) -> tuple[int, str, str]:
    """Один вызов командной строки НАСТОЯЩИМ входом процесса (`cli.run`)."""
    proc = subprocess.run(
        [sys.executable, "-m", "codebugs.cli", "--tracker-root", root, *argv],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _cli_snapshot() -> list[str]:
    rows: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "tracker")
        os.makedirs(root)
        empty = os.path.join(tmp, "elsewhere")
        os.makedirs(empty)

        _cli(root, "init")

        scenarios = [
            ("ok_create", root, ("reqs-add", "FR-1", "-d", "первое")),
            ("duplicate_id", root, ("reqs-add", "FR-1", "-d", "второе")),
            ("bad_priority", root, ("reqs-add", "FR-2", "-d", "x", "--priority", "bogus")),
            ("bad_status", root, ("reqs-add", "FR-3", "-d", "x", "--status", "bogus")),
            ("missing_tracker", empty, ("reqs-add", "FR-4", "-d", "x")),
        ]
        for name, where, argv in scenarios:
            rc, out, err = _cli(where, *argv)
            channel = err if err.strip() else out
            last = [ln for ln in channel.splitlines() if ln.strip()]
            tail = _normalize(last[-1], tmp) if last else ""
            rows.append(
                f"cli  {name:<16} rc={rc} shape={_shape_of_stream(err):<11} last={tail}"
            )
    return rows


# --- поверхность протокола ----------------------------------------------


def _over_the_wire(built: MCPServer, name: str, arguments: dict) -> tuple[bool, str]:
    """Прогон через настоящую клиентскую сессию — заимствован, а не переписан.

    Вся асинхронная обвязка живёт в `tests/test_cb310_refusal_text.py`, откуда
    она сюда и импортируется (образец такого заимствования из ручного скрипта —
    `tests/manual/measure_cb157_forwarding.py`). Копировать её было нельзя: она
    дважды обращается к `built._lowlevel_server`, то есть к внутренности чужой
    библиотеки, две допущенные версии которой в этом проекте уже ведут себя
    по-разному, — и при её поломке копия в наборе тестов покраснела бы на
    ближайшем прогоне, а копия здесь молчала бы, потому что этот скрипт
    запускают руками и ровно в ту минуту, когда им хотят ДОКАЗАТЬ, что починка
    сработала.

    Здесь добавлено ровно одно: перехват ошибки УРОВНЯ ПРОТОКОЛА. Незаявленное
    имя аргумента отбивает промежуточный слой `server.install_strict_arguments`,
    и клиент получает не результат вызова, а отказ протокола — отдельная форма
    ответа, которую снимок обязан различать, а не падать на ней.
    """
    try:
        return call_over_the_wire(built, name, arguments)
    except BaseException as exc:  # noqa: BLE001 - форма ответа и есть предмет
        return True, f"[protocol-error] {_first_message(exc)}"


def _first_message(exc: BaseException) -> str:
    """Текст самой внутренней ошибки — группы исключений разворачиваются."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return _first_message(inner[0])
    return str(exc)


class _Capturing:
    """Заглушка регистратора, запоминающая функции вместо их регистрации.

    Нужна ровно затем, чтобы достать УЖЕ ОБЁРНУТОЕ тело инструмента: обе
    настоящие обёртки (`_NormalizedDescriptions`, `_RefusalsReachTheClient`)
    компонуются в `server.build_registrar`, и если подставить эту заглушку в
    самый низ стопки, наружу выйдет ровно та функция, которую получил бы SDK.
    Внутренности SDK при этом не читаются — на двух допущенных версиях
    библиотеки они могли бы различаться, а стопка обёрток принадлежит пакету.
    """

    def __init__(self) -> None:
        self.registered: dict = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.registered[fn.__name__] = fn
            return fn

        return register


def _raised_class(factory, arguments: dict) -> str:
    """Класс исключения, выходящего из тела инструмента; зачем — см. шапку модуля."""
    from codebugs import reqs

    sink = _Capturing()
    reqs.register_tools(server.build_registrar(sink), factory)
    try:
        sink.registered["reqs_add"](**arguments)
    except BaseException as exc:  # noqa: BLE001 - снимок именно про класс
        return type(exc).__module__ + "." + type(exc).__name__
    return "-"


#: Сценарии протокола. Порядок значим: `duplicate_id` осмыслен только после
#: `ok_create`, поэтому последовательность проигрывается целиком на каждом из
#: двух независимых трекеров — иначе вторая проба увидела бы состояние,
#: оставленное первой, и «удачное создание» само стало бы повтором.
_MCP_SCENARIOS = [
    ("ok_create", "project", {"req_id": "FR-1", "description": "первое"}),
    ("duplicate_id", "project", {"req_id": "FR-1", "description": "второе"}),
    ("bad_priority", "project", {"req_id": "FR-2", "description": "x", "priority": "bogus"}),
    ("bad_status", "project", {"req_id": "FR-3", "description": "x", "status": "bogus"}),
    ("missing_tracker", "elsewhere", {"req_id": "FR-4", "description": "x"}),
    ("undeclared_arg", "project", {"req_id": "FR-5", "description": "x", "bogus_arg": 1}),
]


def _fresh_pair(tmp: str, tag: str) -> tuple[str, str]:
    """Трекер с базой и каталог без неё — независимая пара под одну пробу."""
    project = os.path.join(tmp, f"project-{tag}")
    os.makedirs(project)
    db.init_project(project)
    empty = os.path.join(tmp, f"elsewhere-{tag}")
    os.makedirs(empty)
    return project, empty


def _mcp_snapshot() -> list[str]:
    rows: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        wire_project, wire_empty = _fresh_pair(tmp, "wire")
        class_project, class_empty = _fresh_pair(tmp, "class")

        def factory_of(where: str, project: str, empty: str):
            path = project if where == "project" else empty

            @contextlib.contextmanager
            def factory():
                conn = db.connect(path)
                try:
                    yield conn
                finally:
                    conn.close()

            return factory

        for name, where, arguments in _MCP_SCENARIOS:
            built = server._build_server("reqs", factory_of(where, wire_project, wire_empty))
            is_error, text = _over_the_wire(built, "reqs_add", arguments)
            text = _normalize(text, tmp)
            if not is_error:
                shape = "result"
            elif text.startswith("[protocol-error]"):
                shape = "protocol"
            elif text.strip() == "Error executing tool reqs_add":
                shape = "anonymous"
            else:
                shape = "text"
            if name == "undeclared_arg":
                raised = "(не доходит до тела — отбивает промежуточный слой)"
            else:
                raised = _raised_class(factory_of(where, class_project, class_empty), arguments)
            rows.append(
                f"mcp  {name:<16} is_error={str(is_error):<5} shape={shape:<9} "
                f"raised={raised}\n"
                f"     {name:<16} text={text.replace(chr(10), ' / ')}"
            )
    return rows


def main() -> None:
    import importlib.metadata as md

    print("# снимок договора об ошибках, поверхность reqs-add / reqs_add (CB-316)")
    print(f"# mcp={md.version('mcp')} python={sys.version.split()[0]}")
    print("#")
    for row in _cli_snapshot():
        print(row)
    for row in _mcp_snapshot():
        print(row)


if __name__ == "__main__":
    main()
