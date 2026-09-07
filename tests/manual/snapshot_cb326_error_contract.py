"""CB-326: снимок наблюдаемого КЛИЕНТОМ договора на границе проверки аргументов.

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Пункт 4 критерия приёмки юнита Т-146 требует
зафиксировать договор ДО правки и сверить ПОСЛЕ. Требование не бюрократическое:
`src/codebugs/CLAUDE.md` сам признаёт, что эталонный снимок схемы протокола
(`tests/golden/mcp_schema.json`) НЕ является гейтом на форму ответа — никакой
`outputSchema` не снимается, а живая схема несёт `additionalProperties: True`.
Значит внутренний набор тестов может остаться зелёным при изменении того, что
видит клиент, и единственный способ такое изменение заметить — снять два
снимка и сравнить их построчно. Образец — `snapshot_cb316_error_contract.py`,
приземлённый юнитом Т-143; здесь тот же приём применён к другой границе, и два
раздела добавлены по причинам, изложенным ниже.

ЧТО СНИМАЕТСЯ, И ПОЧЕМУ ИМЕННО ЭТО.

Раздел «протокол» — одиннадцать случаев на поверхности находок, и каждый
снимается ДВАЖДЫ: под старой редакцией протокола (сеанс поднят через
`initialize()`) и под текущей (через `discover()`), где у результата вызова есть
обязательное поле `resultType`.

ЭТА ВТОРАЯ ОСЬ ДОБАВЛЕНА НЕ ДЛЯ ПОЛНОТЫ, А ПОТОМУ ЧТО ЕЁ ОТСУТСТВИЕ УЖЕ СТОИЛО
ОДНОЙ НЕГОДНОЙ ПОЧИНКИ. Первый круг работы по CB-326 собирал ответ руками из
полей `content` и `isError`; под старой редакцией это проходило, под текущей
клиентская библиотека отвергала такой ответ своим же проверяльщиком, и вызов
поднимал исключение вместо возврата значения. Снимок, знавший только старую
редакцию, объявил тогда, что наблюдаемый клиентом договор не изменился, — и
ошибся, потому что смотрел вдоль одной оси из двух. Снимок, который не умеет
покраснеть на смене канала доставки отказа, не выполняет своего назначения.

Из одиннадцати случаев четыре описывает сам бриф, три добавлены вторым кругом
(вызов вовсе без аргументов, вызов с пустым отображением, смешанный случай
«пропущено плюс неверный тип»), остальные — КОНТРОЛЬНЫЕ: правка обязана их не
задеть, а снимок, где нет ни одного случая, который обязан остаться неизменным,
не умеет отличить «ничего лишнего не сломалось» от «снимок просто не туда
смотрит».

  - `ok_add`            — удачное добавление; контрольный.
  - `missing_required`  — не подано обязательное поле; ПРЕДМЕТ карты.
  - `missing_two`       — не подано ДВА обязательных поля; предмет карты, и
                          отдельным случаем потому, что от него зависит форма
                          текста: перечисление против одного имени.
  - `wrong_type`        — неверный тип объявленного поля; вторая половина
                          предмета, и в снимке она нужна даже если правка её
                          не трогает, потому что тогда снимок и будет тем
                          местом, где записано, что она осталась прежней.
  - `undeclared_arg`    — незаявленное имя аргумента; контрольный, и самый
                          важный из них: бриф прямо запрещает сливать этот
                          канал с предыдущими.
  - `domain_refusal`    — отказ доменного слоя; контрольный.
  - `unknown_tool`      — несуществующее имя инструмента; контрольный. Слой
                          строгих имён аргументов НАМЕРЕННО оставляет ответ на
                          него чужой библиотеке, и это записано в его коде.
  - `missing_tracker`   — трекера нет в этом каталоге; контрольный, другой род
                          отказа (поднимается при ОТКРЫТИИ трекера).

Раздел «учёт вызовов» добавлен сверх образца Т-143 и объясняется так. Сервер
считает вызовы инструментов в таблицу `tool_calls` (её показывает команда
`codebugs usage`), и делает это тоже промежуточным слоем. Новый слой, если
поставить его СНАРУЖИ учётного, отнимет у учёта те вызовы, которые тот считает
сегодня, — а улика самой карты CB-326 («инструмент `add` отказывает в 11
процентах вызовов») взята именно из этой таблицы. То есть неудачное размещение
слоя молча обесценило бы основание карты, и при этом не изменило бы ни одного
байта в ответе клиенту. Раздел существует, чтобы такое изменение было видно.

Раздел «командная строка» тоже контрольный целиком. Проверку аргументов там
делает `argparse`, а не этот пакет, так что правка не должна двигать её ни на
байт; раздел — способ это показать, а не предположить.

КАК ЗАПУСКАТЬ (только из своего рабочего дерева, под обеими версиями):

    uv run --extra dev python tests/manual/snapshot_cb326_error_contract.py > <файл>
    uv run --with 'mcp==2.1.1' --extra dev python \
        tests/manual/snapshot_cb326_error_contract.py > <файл>

Координаты прогона (временные каталоги, метки времени, длительности) заменяются
заглушками, иначе два прогона несравнимы обычным `diff`.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from codebugs import db, server, usage

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from test_cb310_refusal_text import call_over_the_wire  # noqa: E402

# --- общее ---------------------------------------------------------------

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_FINDING_ID = re.compile(r"\bCB-\d+\b")
_COMMIT_SHA = re.compile(r"\b[0-9a-f]{40}\b")


def _normalize(text: str, root: str) -> str:
    """Убрать координаты прогона: временный каталог, метки времени, снимок ветки.

    Снимок ветки (`reported_at_commit`) нормализуется по причине, которую легко
    упустить и дорого обнаружить поздно: удачное добавление записывает в находку
    текущий коммит рабочего дерева, а между снимком «до» и снимком «после» на
    ветке неизбежно появляются коммиты. Без этой замены сравнение показывало бы
    расхождение ВСЕГДА — и настоящее расхождение утонуло бы в заведомом.
    """
    out = text.replace(os.path.realpath(root), "<TMP>").replace(root, "<TMP>")
    out = _TIMESTAMP.sub("<TS>", out)
    return _COMMIT_SHA.sub("<SHA>", out)


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


# --- поверхность протокола ----------------------------------------------


def _over_the_wire(built, name: str, arguments: dict | None, protocol: str) -> tuple[bool, str]:
    """Прогон через настоящую клиентскую сессию; ошибка ПРОТОКОЛА — отдельная форма.

    Асинхронная обвязка заимствована из `tests/test_cb310_refusal_text.py`, а не
    переписана здесь, по причине, изложенной в образце Т-143: она обращается к
    внутренности чужой библиотеки, две допущенные версии которой уже ведут себя
    по-разному, и копия в наборе тестов покраснела бы при её поломке, а копия
    здесь молчала бы — потому что этот скрипт запускают руками и ровно тогда,
    когда им хотят ДОКАЗАТЬ, что починка сработала.
    """
    try:
        return call_over_the_wire(built, name, arguments, protocol=protocol)
    except BaseException as exc:  # noqa: BLE001 — форма ответа и есть предмет
        return True, f"[protocol-error] {_first_message(exc)}"


def _first_message(exc: BaseException) -> str:
    """Текст самой внутренней ошибки — группы исключений разворачиваются."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return _first_message(inner[0])
    return str(exc)


#: Случаи протокола. Пояснение к каждому — в шапке модуля.
_MCP_SCENARIOS = [
    # `new_category` обязателен: в пустом трекере любая категория новая, а её
    # появление огорожено отдельным гейтом (CB-60). Без флага «удачный» случай
    # был бы отказом и ничего бы не контролировал.
    ("ok_add", "project", "add", {"severity": "low", "category": "x", "file": "y",
                                  "description": "z", "new_category": True}),
    ("missing_required", "project", "add", {"category": "x", "file": "y", "description": "z"}),
    ("missing_two", "project", "add", {"category": "x", "description": "z"}),
    # `None` означает «поле аргументов не посылать вовсе» — на проводе это не то
    # же самое, что пустое отображение, и путать их сервер не имеет права.
    ("no_arguments", "project", "add", None),
    ("empty_arguments", "project", "add", {}),
    ("missing_plus_wrong_type", "project", "add", {"category": "x", "file": "y", "severity": 5}),
    ("wrong_type", "project", "add", {"severity": 5, "category": "x", "file": "y",
                                      "description": "z"}),
    ("undeclared_arg", "project", "add", {"severity": "low", "category": "x", "file": "y",
                                          "description": "z", "bogus": 1}),
    ("domain_refusal", "project", "update", {"finding_id": "CB-1", "status": "bogus"}),
    ("unknown_tool", "project", "no_such_tool", {"anything": 1}),
    ("missing_tracker", "elsewhere", "add", {"severity": "low", "category": "x", "file": "y",
                                             "description": "z"}),
]


def _shape_of_answer(is_error: bool, text: str, tool: str) -> str:
    """Одно слово о том, КАКОЙ формы ответ получил клиент.

    Различаются четыре формы, и различие несёт смысл, а не украшает вывод:
    `result` — обычный ответ; `protocol` — отказ уровня протокола, который в
    клиентской библиотеке поднимается исключением, то есть меняет способ, каким
    вызывающий обязан его обрабатывать; `anonymous` — результат с признаком
    ошибки, но БЕЗ причины (это и есть беда, ради которой заведена карта
    CB-310); `text` — результат с признаком ошибки и с текстом.
    """
    if not is_error:
        return "result"
    if text.startswith("[protocol-error]"):
        return "protocol"
    if text.strip() == f"Error executing tool {tool}":
        return "anonymous"
    return "text"


def _foreign_marks(text: str) -> str:
    """Признаки того, что текст пришёл не от этого проекта.

    Снимается отдельной колонкой, потому что пункт 2 критерия приёмки
    сформулирован именно так: в ответе не должно быть ссылки на сайт чужой
    библиотеки. Колонка делает выполнение или невыполнение этого пункта видимым
    в самом снимке, а не выводимым из текста глазами.
    """
    marks = []
    if "errors.pydantic.dev" in text:
        marks.append("ссылка-на-чужой-сайт")
    # Единственное и множественное число — «1 validation error» против
    # «2 validation errors», — поэтому признак ищется по корню, а не по фразе
    # целиком: на двух пропущенных полях полная фраза не совпала бы, и снимок
    # молча объявил бы чужой текст своим.
    if "validation error" in text:
        marks.append("формулировка-чужой-библиотеки")
    if "Arguments" in text:
        marks.append("имя-внутренней-модели")
    return ",".join(marks) if marks else "-"


def _fresh_pair(tmp: str, tag: str) -> tuple[str, str]:
    """Трекер с базой и каталог без неё — независимая пара под одну пробу."""
    project = os.path.join(tmp, f"project-{tag}")
    os.makedirs(project)
    db.init_project(project)
    empty = os.path.join(tmp, f"elsewhere-{tag}")
    os.makedirs(empty)
    return project, empty


def _factory_of(path: str):
    @contextlib.contextmanager
    def factory():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    return factory


def _mcp_snapshot() -> list[str]:
    rows: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        project, empty = _fresh_pair(tmp, "wire")
        # ОСЬ РЕДАКЦИИ ПРОТОКОЛА. `initialize()` согласует старую редакцию,
        # `discover()` — текущую, где у результата вызова есть обязательное поле
        # `resultType`. Снимок обязан ходить по обеим, и это не запас прочности:
        # первый круг починки CB-326 был зелен под старой редакцией и ронял
        # клиента под текущей, а снимок, знавший только старую, объявил тогда,
        # что договор не изменился.
        for protocol in ("initialize", "discover"):
            for name, where, tool, arguments in _MCP_SCENARIOS:
                # Свой сервер на каждый случай: слой строгих имён аргументов
                # запоминает каталог инструментов при первом вызове, и общий
                # сервер сделал бы порядок случаев значимым.
                built = server._build_server("findings", _factory_of(project if where == "project"
                                                                    else empty))
                is_error, text = _over_the_wire(built, tool, arguments, protocol)
                text = _normalize(text, tmp)
                shape = _shape_of_answer(is_error, text, tool)
                # Идентификатор находки нормализуется только в удачном ответе:
                # там он выдаётся трекером и меняется от прогона к прогону,
                # тогда как в отказе он приходит от вызывающего и обязан быть
                # виден дословно.
                body = _FINDING_ID.sub("<CB-N>", text) if not is_error else text
                tag = f"{protocol[:4]}/{name}"
                rows.append(
                    f"mcp  {tag:<26} is_error={str(is_error):<5} shape={shape:<9} "
                    f"foreign={_foreign_marks(text)}\n"
                    f"     {tag:<26} text={body.replace(chr(10), ' / ')}"
                )
    return rows


# --- учёт вызовов --------------------------------------------------------


def _usage_snapshot() -> list[str]:
    """Что учётная таблица записывает на каждый случай; зачем — см. шапку модуля."""
    rows: list[str] = []
    for name, where, tool, arguments in _MCP_SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            project, empty = _fresh_pair(tmp, "usage")
            factory = _factory_of(project if where == "project" else empty)
            built = server._build_server("findings", factory)
            # Одной редакции протокола здесь достаточно, и это обосновано, а не
            # сэкономлено: учёт ведёт промежуточный слой, который стоит до
            # всякой сериализации ответа, поэтому редакция протокола на него не
            # влияет. Раздел выше ходит по обеим именно потому, что там предмет
            # — форма ОТВЕТА, а она от редакции зависит.
            _over_the_wire(built, tool, arguments, "initialize")
            # Учёт пишет в ТОТ ЖЕ трекер, что и инструмент, поэтому на случае
            # «трекера нет» записывать некуда — это не пробел снимка, а прямое
            # следствие правила «учёт никогда не роняет вызов».
            try:
                with factory() as conn:
                    counted = usage.usage_summary(conn)["rows"]
            except BaseException as exc:  # noqa: BLE001
                rows.append(f"use  {name:<16} <учёт недоступен: {type(exc).__name__}>")
                continue
        if not counted:
            rows.append(f"use  {name:<16} не посчитан")
        else:
            rows.append(
                f"use  {name:<16} "
                + "; ".join(f"{r['tool_name']}: calls={r['calls']} failures={r['failures']}"
                            for r in counted)
            )
    return rows


# --- поверхность командной строки ---------------------------------------


def _cli(root: str, *argv: str) -> tuple[int, str, str]:
    """Один вызов НАСТОЯЩИМ входом процесса (`cli.run`), как в образце Т-143."""
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
        _cli(root, "init")

        scenarios = [
            ("ok_add", ("add", "-s", "low", "-c", "x", "-f", "y", "-d", "z")),
            ("missing_required", ("add", "-c", "x", "-f", "y", "-d", "z")),
            ("undeclared_arg", ("add", "-s", "low", "-c", "x", "-f", "y", "-d", "z", "--bogus")),
            ("bad_severity", ("add", "-s", "bogus", "-c", "x", "-f", "y", "-d", "z")),
            ("domain_refusal", ("update", "CB-1", "--status", "bogus")),
        ]
        for name, argv in scenarios:
            rc, out, err = _cli(root, *argv)
            channel = err if err.strip() else out
            last = [ln for ln in channel.splitlines() if ln.strip()]
            tail = _FINDING_ID.sub("<CB-N>", _normalize(last[-1], tmp)) if last else ""
            rows.append(f"cli  {name:<16} rc={rc} shape={_shape_of_stream(err):<11} last={tail}")
    return rows


def main() -> None:
    import importlib.metadata as md

    print("# снимок договора об ошибках на границе проверки аргументов (CB-326)")
    print(f"# mcp={md.version('mcp')} pydantic={md.version('pydantic')} "
          f"python={sys.version.split()[0]}")
    print("#")
    for row in _mcp_snapshot():
        print(row)
    print("#")
    for row in _usage_snapshot():
        print(row)
    print("#")
    for row in _cli_snapshot():
        print(row)


if __name__ == "__main__":
    main()
